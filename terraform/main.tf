##############################################################
# main.tf - GKE + Vault auto-unseal referencing an existing
# Cloud SQL instance for your app.
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
  # Credentials are provided via the GOOGLE_APPLICATION_CREDENTIALS
  # environment variable (or via gcloud auth).
}

provider "kubernetes" {
  host = "https://${google_container_cluster.primary.endpoint}"
  cluster_ca_certificate = base64decode(
    google_container_cluster.primary.master_auth.0.cluster_ca_certificate
  )
  token = data.google_client_config.default.access_token
}

provider "helm" {
  kubernetes {
    host = "https://${google_container_cluster.primary.endpoint}"
    cluster_ca_certificate = base64decode(
      google_container_cluster.primary.master_auth.0.cluster_ca_certificate
    )
    token = data.google_client_config.default.access_token
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
    image_type   = "COS_CONTAINERD"
  }

  # Ignore ephemeral changes GCP tries to force, so we don't get the 400 error
  lifecycle {
    ignore_changes = [
      node_config[0].resource_labels,
      node_config[0].kubelet_config,
    ]
  }
}

##################################
# 2) Create KMS Key Ring & Key for Vault Auto-Unseal
##################################
resource "google_kms_key_ring" "vault_ring" {
  name     = var.kms_key_ring
  project  = var.gcp_project
  location = var.gcp_region

  # If it already exists, we import or ignore changes
  lifecycle {
    ignore_changes = [name, project, location]
  }
}

resource "google_kms_crypto_key" "vault_key" {
  name            = var.kms_crypto_key
  key_ring        = google_kms_key_ring.vault_ring.id
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

  values = [
    file("${path.module}/vault-values.yaml")  # <= Set 'service.type=LoadBalancer'
  ]

  depends_on = [
    google_container_node_pool.primary_nodes,
    kubernetes_secret.vault_gcp_key
  ]
  timeout = 600
  wait    = true
}

##################################
# 4) Auto-Init Vault & Configure DB Secrets
##################################
resource "random_password" "vault_root_token" {
  length  = 32
  special = true
}

data "google_sql_database_instance" "existing_db" {
  name    = var.cloud_sql_instance_name
  project = var.gcp_project
}

resource "null_resource" "vault_init_and_config" {
  depends_on = [helm_release.vault]

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command = <<-EOF
      #!/usr/bin/env bash
      set -euo pipefail

      echo "Waiting for Vault LoadBalancer external IP..."
      EXTERNAL_IP=""
      for i in {1..30}; do
        EXTERNAL_IP=$(kubectl get svc vault -n vault -o jsonpath="{.status.loadBalancer.ingress[0].ip}" || true)
        if [ -n "$EXTERNAL_IP" ]; then
          echo "Vault is available at external IP: $EXTERNAL_IP"
          break
        else
          echo "External IP not assigned yet, sleeping 10s..."
          sleep 10
        fi
      done

      if [ -z "$EXTERNAL_IP" ]; then
        echo "Vault external IP not assigned, aborting"
        exit 1
      fi

      export VAULT_ADDR="http://$EXTERNAL_IP:8200"
      echo "Using VAULT_ADDR=$VAULT_ADDR"

      echo "Waiting for Vault to become available at $VAULT_ADDR..."
      RETRY=0
      until curl -s $VAULT_ADDR/v1/sys/health; do
        echo "Vault not available, retrying in 5 seconds..."
        sleep 5
        RETRY=$((RETRY+1))
        if [ $RETRY -ge 6 ]; then
          echo "Vault did not become available, aborting"
          exit 1
        fi
      done

      echo "Initializing Vault if not already initialized..."
      if vault status -address=$VAULT_ADDR 2>/dev/null | grep -q 'Initialized.*true'; then
        echo "Vault already initialized, skipping operator init."
      else
        echo "Initializing Vault..."
        INIT_OUT=$(vault operator init -key-shares=1 -key-threshold=1 -format=json -address=$VAULT_ADDR)
        UNSEAL_KEY=$(echo "$INIT_OUT" | jq -r .unseal_keys_b64[0])
        ROOT_TOKEN=$(echo "$INIT_OUT" | jq -r .root_token)

        vault operator unseal -address=$VAULT_ADDR "$UNSEAL_KEY"
        echo "Vault unsealed."

        vault login -address=$VAULT_ADDR "$ROOT_TOKEN"
        vault token create -id="${random_password.vault_root_token.result}" -policy="root" -ttl=87600h -address=$VAULT_ADDR
        vault login -address=$VAULT_ADDR "${random_password.vault_root_token.result}"
      fi

      echo "Enabling Kubernetes auth method..."
      vault auth enable -address=$VAULT_ADDR kubernetes || true

      vault write -address=$VAULT_ADDR auth/kubernetes/config \
        kubernetes_host="https://kubernetes.default.svc.cluster.local:443" \
        token_reviewer_jwt="$(cat /run/secrets/kubernetes.io/serviceaccount/token)" \
        kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt

      echo "Enabling database secrets engine..."
      vault secrets enable -address=$VAULT_ADDR database || true

      echo "Configuring database secrets engine..."
      INSTANCE_CONN_NAME=$(gcloud sql instances describe "${var.cloud_sql_instance_name}" --project "${var.gcp_project}" --format 'value(connectionName)')
      DB_USER="root"
      DB_PASS="${var.db_root_password}"

      # Configure the database connection
      vault write -address=$VAULT_ADDR database/config/my-sql-db \
        plugin_name="mysql-legacy-database-plugin" \
        connection_url='{{username}}:{{password}}@tcp('"$INSTANCE_CONN_NAME"')/' \
        username="$DB_USER" \
        password="$DB_PASS"

      # Create a role for generating dynamic credentials
      vault write -address=$VAULT_ADDR database/roles/my-app-role \
        db_name="my-sql-db" \
        creation_statements="CREATE USER '{{name}}'@'%' IDENTIFIED BY '{{password}}'; GRANT ALL PRIVILEGES ON *.* TO '{{name}}'@'%';" \
        default_ttl="1h" \
        max_ttl="24h"

      echo "Vault auto-initialization complete."
    EOF
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
