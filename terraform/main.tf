##############################################################
# main.tf
# Deploys a GKE cluster, Vault with GCP KMS auto-unseal,
# and stores/retrieves Vault root token in Secret Manager.
# Also reads that token back from GSM into a K8S Secret.
##############################################################

terraform {
  required_version = ">=1.3.0"
  backend "gcs" {
    bucket = "tfstate-bucket-instamini"
    prefix = "terraform/state"
  }
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 4.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.16"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.8"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.4"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

provider "google" {
  project = var.gcp_project
  region  = var.gcp_region
  zone    = var.gcp_zone
}

provider "kubernetes" {
  host                   = "https://${google_container_cluster.primary.endpoint}"
  cluster_ca_certificate = base64decode(
    google_container_cluster.primary.master_auth.0.cluster_ca_certificate
  )
  token = data.google_client_config.default.access_token
}

provider "helm" {
  kubernetes {
    host                   = "https://${google_container_cluster.primary.endpoint}"
    cluster_ca_certificate = base64decode(
      google_container_cluster.primary.master_auth.0.cluster_ca_certificate
    )
    token = data.google_client_config.default.access_token
  }
}

data "google_client_config" "default" {}

############################################################
# 1) GKE Cluster & Node Pool
############################################################
resource "google_container_cluster" "primary" {
  name                     = "vault-ha-cluster"
  location                 = var.gcp_zone
  remove_default_node_pool = true
  initial_node_count       = 1

  master_auth {
    client_certificate_config {
      issue_client_certificate = false
    }
  }
}

resource "google_container_node_pool" "primary_nodes" {
  depends_on = [google_container_cluster.primary]
  name       = "primary-pool"
  location   = var.gcp_zone
  cluster    = google_container_cluster.primary.name
  node_count = 2

  node_config {
    machine_type = "e2-standard-2"
    disk_size_gb = 50
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    image_type   = "COS_CONTAINERD"
  }

  lifecycle {
    ignore_changes = [
      node_config[0].resource_labels,
      node_config[0].kubelet_config,
    ]
  }
}

############################################################
# 2) KMS Key Ring & Crypto Key
############################################################
resource "google_kms_key_ring" "vault_ring" {
  name     = var.kms_key_ring
  project  = var.gcp_project
  location = var.gcp_region

  lifecycle {
    ignore_changes = [name, project, location]
  }
}

resource "google_kms_crypto_key" "vault_key" {
  name            = var.kms_crypto_key
  key_ring        = google_kms_key_ring.vault_ring.id
  rotation_period = "7776000s" # 90 days
}

############################################################
# 3) Deploy Vault with auto-unseal
############################################################
resource "kubernetes_namespace" "vault_ns" {
  metadata {
    name = "vault"
  }
}

resource "kubernetes_secret" "vault_gcp_key" {
  metadata {
    name      = "gcp-key"
    namespace = kubernetes_namespace.vault_ns.metadata[0].name
  }
  data = {
    # This is your GCP service account key JSON, base64-encoded
    "key.json" = var.vault_gcp_sa_key_b64
  }
}

resource "helm_release" "vault" {
  name             = "vault"
  namespace        = kubernetes_namespace.vault_ns.metadata[0].name
  repository       = "https://helm.releases.hashicorp.com"
  chart            = "vault"
  version          = "0.23.0"
  create_namespace = false
  force_update = true

  values = [
    file("${path.module}/vault-values.yaml")
  ]

  depends_on = [
    google_container_node_pool.primary_nodes,
    kubernetes_secret.vault_gcp_key
  ]
  timeout = 600
  wait    = true
}

############################################################
# 4) Create Secret in GCP Secret Manager (for stable root token)
############################################################
resource "google_secret_manager_secret" "root_token_secret" {
  secret_id = "vault-${var.kms_crypto_key}-token"
  project   = var.gcp_project

  replication {
    automatic = true
  }
}

############################################################
# 5) Random Root Token & local-exec Init
############################################################
resource "random_password" "vault_root_token" {
  length  = 32
  special = true
}

data "google_sql_database_instance" "existing_db" {
  name    = var.cloud_sql_instance_name
  project = var.gcp_project
}

# This sets up Vault, initializes it if needed, and stores the token in GSM
resource "null_resource" "vault_init_and_config" {
  depends_on = [
    helm_release.vault,
    google_secret_manager_secret.root_token_secret
  ]

  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command = <<-SCRIPT
      set -euo pipefail

      SECRET_NAME="vault-${var.kms_crypto_key}-token"
      GCP_PROJECT="${var.gcp_project}"

      echo "[Vault-Init] Checking for external LB IP..."
      EXTERNAL_IP=""
      for i in {1..30}; do
        EXTERNAL_IP=$(kubectl get svc vault -n vault -o jsonpath="{.status.loadBalancer.ingress[0].ip}" || true)
        if [ -n "$EXTERNAL_IP" ]; then
          echo "Vault LB IP: $EXTERNAL_IP"
          break
        else
          echo "No external IP yet. Sleeping 10s..."
          sleep 10
        fi
      done

      if [ -z "$EXTERNAL_IP" ]; then
        echo "ERROR: No external IP for Vault LB."
        exit 1
      fi

      export VAULT_ADDR="http://$EXTERNAL_IP:8200"
      echo "[Vault-Init] VAULT_ADDR=$VAULT_ADDR"

      # Wait for Vault readiness
      RETRY=0
      until curl -s $VAULT_ADDR/v1/sys/health; do
        echo "Vault not ready, sleeping 5..."
        sleep 5
        RETRY=$((RETRY+1))
        if [ $RETRY -ge 6 ]; then
          echo "ERROR: Vault never became ready."
          exit 1
        fi
      done

      echo "[Vault-Init] Checking if Vault is already init..."
      set +e
      EXISTING_TOKEN=$(gcloud secrets versions access latest --secret="$SECRET_NAME" --project="$GCP_PROJECT" 2>/dev/null)
      TOKEN_STATUS=$?
      set -e

      # If Vault is already initialized, just log in
      if vault status 2>/dev/null | grep -q 'Initialized.*true'; then
        echo "[Vault-Init] Vault is already initialized."
        if [ $TOKEN_STATUS -eq 0 ] && [ -n "$EXISTING_TOKEN" ]; then
          echo "[Vault-Init] Logging in with existing root token from GSM..."
          vault login "$EXISTING_TOKEN" || {
            echo "ERROR: existing token in GSM is invalid."
            exit 1
          }
        else
          echo "ERROR: Vault is init but no stored root token in GSM. Exiting."
          exit 1
        fi
      else
        echo "[Vault-Init] Vault is not init. Doing operator init now..."

        INIT_OUT=$(vault operator init -key-shares=1 -key-threshold=1 -format=json)
        UNSEAL_KEY=$(echo "$INIT_OUT" | jq -r .unseal_keys_b64[0])
        ROOT_TOKEN=$(echo "$INIT_OUT" | jq -r .root_token)

        vault operator unseal "$UNSEAL_KEY"
        vault login "$ROOT_TOKEN"

        echo "[Vault-Init] Creating stable root token: ${random_password.vault_root_token.result}"
        vault token create -id="${random_password.vault_root_token.result}" -policy="root" -ttl=87600h

        echo "[Vault-Init] Logging in with stable root token..."
        vault login "${random_password.vault_root_token.result}"

        echo "[Vault-Init] Storing stable root token in GCP Secret Manager..."
        echo -n "${random_password.vault_root_token.result}" | gcloud secrets versions add "$SECRET_NAME" --data-file=- --project="$GCP_PROJECT"
      fi

      echo "[Vault-Init] Enable database secrets engine..."
      vault secrets enable database || true

      echo "[Vault-Init] Config DB secrets..."
      INSTANCE_CONN_NAME=$(gcloud sql instances describe "${var.cloud_sql_instance_name}" --project "${var.gcp_project}" --format='value(ipAddresses[0].ipAddress)')
      DB_USER="root"
      DB_PASS="${var.db_root_password}"

      vault write database/config/my-sql-db \
        plugin_name="mysql-legacy-database-plugin" \
        connection_url='{{username}}:{{password}}@tcp('"$INSTANCE_CONN_NAME:3306"')/' \
        username="$DB_USER" \
        password="$DB_PASS" \
        allowed_roles="my-app-role"

      vault write database/roles/my-app-role \
        db_name="my-sql-db" \
        creation_statements="CREATE USER '{{name}}'@'%' IDENTIFIED BY '{{password}}'; GRANT ALL PRIVILEGES ON *.* TO '{{name}}'@'%';" \
        default_ttl="1h" \
        max_ttl="24h"

      echo "[Vault-Init] Done. Root token now stored in Secret Manager."
    SCRIPT
  }
}

############################################################
# Optional: Read stable token from GSM & create K8S secret
# (If you want the token in-cluster for a vault-init-job)
############################################################

# 1) Data source to read the latest token from the same GSM secret
data "google_secret_manager_secret_version" "vault_root_token_latest" {
  project = var.gcp_project
  secret  = google_secret_manager_secret.root_token_secret.secret_id
  version = "latest"
}

# 2) Create K8S secret with that token
resource "kubernetes_secret" "vault_root_token_secret" {
  metadata {
    name      = "vault-root-token"
    namespace = "vault"
  }
  data = {
    "token" = data.google_secret_manager_secret_version.vault_root_token_latest.secret_data
  }
  depends_on = [
    google_container_node_pool.primary_nodes
  ]
}

############################################################
# Outputs
############################################################
output "cluster_name" {
  description = "Name of the GKE cluster"
  value       = google_container_cluster.primary.name
}

output "vault_root_token" {
  description = "The stable root token (randomly generated in TF)."
  value       = random_password.vault_root_token.result
  sensitive   = true
}

output "gke_endpoint" {
  value = google_container_cluster.primary.endpoint
}
