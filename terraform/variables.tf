variable "gcp_project" {
  type = string
  default = "instsmini-auto"
}

variable "gcp_region" {
  type    = string
  default = "us-central1"
}

variable "gcp_zone" {
  type    = string
  default = "us-central1-a"
}

variable "kms_key_ring" {
  type    = string
  default = "vault-ha-keyring"
}

variable "kms_crypto_key" {
  type    = string
  default = "vault-auto-unseal"
}

variable "vault_gcp_sa_key_b64" {
  type        = string
  description = "Base64-encoded JSON of your GCP SA for Vault auto-unseal"
}

variable "cloud_sql_instance_name" {
  type        = string
  description = "socialdb"
}

variable "db_root_password" {
  type        = string
  description = "Database root password"
  sensitive   = true
}
