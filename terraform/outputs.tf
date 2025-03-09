output "cluster_name" {
  value = google_container_cluster.primary.name
}

output "gke_endpoint" {
  value = google_container_cluster.primary.endpoint
}

output "vault_root_token" {
  value       = random_password.vault_root_token.result
  sensitive   = true
}
