##############################################################
# main.tf - GKE + Vault auto-unseal referencing an existing
# Cloud SQL instance for your app.
##############################################################

terraform {
  required_version = ">=1.3.0"
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

  # We'll store state locally for simplicity, but ideally use a remote backend.
  backend "local" {
    path = "terraform.tfstate"
  }
}

provider "google" {
  project = var.gcp_project
  region  = var.gcp_region
  zone    = var.gcp_zone
  # We'll set credentials from the environment or GitHub Action
  # (the file /tmp/gcp-key.json).
}

provider "kubernetes" {
  host                   = google_container_cluster.primary.endpoint
  cluster_ca_certificate = base64decode(
    google_container_cluster.primary.master_auth.0.cluster_ca_certificate
  )
  token                  = data.google_client_config.default.access_token
  load_config_file       = false
}

provider "helm" {
  kubernetes {
    host                   = google_container_cluster.primary.endpoint
    cluster_ca_certificate = base64decode(
      google_container_cluster.primary.master_auth.0.cluster_ca_certificate
    )
    token                  = data.google_client_config.default.access_token
    load_config_file       = false
  }
}

data "google_client_config" "default" {}

##################################
# 1) Create GKE Cluster
##################################
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
  }
}

##################################
# 2) Create KMS Key Ring & Key for Vault Auto-Unseal
##################################
resource "google_kms_key_ring" "vault_ring" {
  name     = var.kms_key_ring
  project  = var.gcp_project
  location = var.gcp_region
}

resource "google_kms_crypto_key" "vault_key" {
  name            = var.kms_crypto_key
  key_ring        = google_kms_key_ring.vault_ring.self_link
  rotation_period = "7776000s" # 90 days
  depends_on      = [google_kms_key_ring.vault_ring]
}

##################################
# 3) Deploy Vault with auto-unseal
##################################
resource "kubernetes_namespace" "vault_ns" {
  metadata {
    name = "vault"
  }
}

# We'll store the GCP SA JSON (for KMS usage) as a secret
resource "kubernetes_secret" "vault_gcp_key" {
  metadata {
    name      = "gcp-key"
    namespace = kubernetes_namespace.vault_ns.metadata[0].name
  }
  data = {
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

  # We'll store additional Helm values in vault-values.yaml
  # to keep the config for auto-unseal & injector.
  values = [
    file("${path.module}/vault-values.yaml")
  ]

  # Pass in GCP auto-unseal values
  set {
    name  = "server.config.seal.gcpckms.project"
    value = var.gcp_project
  }
  set {
    name  = "server.config.seal.gcpckms.region"
    value = var.gcp_region
  }
  set {
    name  = "server.config.seal.gcpckms.key_ring"
    value = var.kms_key_ring
  }
  set {
    name  = "server.config.seal.gcpckms.crypto_key"
    value = var.kms_crypto_key
  }

  depends_on = [
    google_container_node_pool.primary_nodes,
    kubernetes_secret.vault_gcp_key
  ]
  timeout = 600
  wait    = true
}

##################################
# 4) (Optional) Auto-Init Vault & DB Secrets
##################################
resource "random_password" "vault_root_token" {
  length  = 32
  special = true
}

data "google_sql_database_instance" "existing_db" {
  # The name of your Cloud SQL instance
  name    = var.cloud_sql_instance_name
  project = var.gcp_project
}

resource "null_resource" "vault_init_and_config" {
  depends_on = [helm_release.vault]

  provisioner "local-exec" {
    command = <<EOT
#!/usr/bin/env bash
set -euo pipefail

echo "Waiting for vault-0 pod to be Running..."
for i in {1..30}; do
  PHASE=$(kubectl get pod vault-0 -n vault -o jsonpath='{.status.phase}' || true)
  if [ "$PHASE" = "Running" ]; then
    echo "vault-0 is running!"
    break
  else
    echo "vault-0 not ready, found phase=$PHASE, sleeping 10s"
    sleep 10
  fi
done

# Port-forward to Vault temporarily
kubectl port-forward vault-0 8200:8200 -n vault &
PF_PID=$!
sleep 5

# Check if Vault is initialized
if vault status 2>/dev/null | grep -q 'Initialized.*true'; then
  echo "Vault already initialized, skipping operator init."
else
  echo "Initializing Vault..."
  INIT_OUT=$(vault operator init -key-shares=1 -key-threshold=1 -format=json)
  UNSEAL_KEY=$(echo "$INIT_OUT" | jq -r .unseal_keys_b64[0])
  ROOT_TOKEN=$(echo "$INIT_OUT" | jq -r .root_token)

  # Vault auto-unseals with KMS, but let's do a manual unseal once
  vault operator unseal "$UNSEAL_KEY"
  echo "Vault unsealed with share."

  # Create a new stable root token
  vault login "$ROOT_TOKEN"
  vault token create -id="${random_password.vault_root_token.result}" -policy="root" -ttl=87600h
  vault login "${random_password.vault_root_token.result}"
fi

echo "Enabling Kubernetes auth method..."
vault auth enable kubernetes || true

vault write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc.cluster.local:443" \
  token_reviewer_jwt="$(cat /run/secrets/kubernetes.io/serviceaccount/token)" \
  kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt

echo "Enabling database secrets engine..."
vault secrets enable database || true

INSTANCE_CONN_NAME=$(gcloud sql instances describe ${var.cloud_sql_instance_name} \
  --project ${var.gcp_project} --format 'value(connectionName)')

DB_USER="root"
DB_PASS="${var.db_root_password}"

vault write database/config/my-sql-db \
  plugin_name="mysql-legacy-database-plugin" \
  connection_url="{{username}}:{{password}}@tcp(${INSTANCE_CONN_NAME})/" \
  username="$DB_USER" \
  password="$DB_PASS"

vault write database/roles/my-app-role \
  db_name="my-sql-db" \
  creation_statements="CREATE USER '{{name}}'@'%' IDENTIFIED BY '{{password}}'; GRANT ALL PRIVILEGES ON *.* TO '{{name}}'@'%';" \
  default_ttl="1h" \
  max_ttl="24h"

kill $PF_PID || true
echo "Vault DB secrets engine configured!"
EOT
  }
}

############################################################
# Outputs
############################################################
output "cluster_name" {
  value = google_container_cluster.primary.name
}

output "vault_root_token" {
  description = "The random Vault root token that we set"
  value       = random_password.vault_root_token.result
  sensitive   = true
}

output "gke_endpoint" {
  value = google_container_cluster.primary.endpoint
}
