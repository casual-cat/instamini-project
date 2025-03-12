# DevOps Final Project: CI/CD Pipeline with Terraform, Vault, Helm, Argo CD & GitHub Actions
# WELCOME TO INSTA-MINI!

<div align="center">
  <img src="https://user-images.githubusercontent.com/8144303/226272497-6eb8f4ed-2e21-45cf-bcae-d6834fd2a95e.png" width="60%" alt="DevOps Pipeline Diagram" />
</div>

<br />

## Overview

**Goal**: Demonstrate a full DevOps workflow for a Python-based application using:
1. **GitHub Actions** – Automated CI/CD
2. **Docker** – Containerization
3. **Terraform** – Provisioning a GKE cluster
4. **Vault** – Secrets management (auto-init & unseal via GCP KMS)
5. **Helm** – Packaging K8s manifests
6. **Argo CD** – GitOps-based deployment

<br />

## Key Features

- **Continuous Integration**:  
  - Builds & tests your Python Flask app via GitHub Actions.
  - Uses `unittest discover` to run tests under `tests/`.
- **Continuous Deployment**:  
  - Provisions/upgrades a GKE cluster and Vault using Terraform.
  - Deploys your Dockerized app to the cluster using a Helm chart.
  - Argo CD auto-syncs your Helm chart changes on every push.
- **Vault**:  
  - Automatically initialized and unsealed with GCP KMS.
  - Vault root token stored in Google Secret Manager.
  - (Optional) Configurable for dynamic or static DB credentials.
- **Docker**:  
  - Dockerfile packaging the Python app, pushed to Docker Hub.
- **Secrets**:  
  - All sensitive info stored securely in GitHub Secrets or K8s Secrets.

<br />

## Repository Structure

```
.
├── .github/
│   └── workflows/
│       └── ci-cd.yml         # GitHub Actions pipeline
├── docker-compose.yaml        # (Optional) for local dev, if any
├── Dockerfile                 # Builds the Flask app container
├── requirements.txt           # Python dependencies
├── app.py                     # Main Flask application
├── tests/                     # Python unit tests
├── terraform/
│   ├── main.tf                # GKE, Vault, Secret Manager configs
│   ├── variables.tf
│   ├── vault-values.yaml      # Helm values override for Vault
│   ├── vault-init-job.yaml    # (Optional) to configure Vault K8s auth
│   └── ...
├── helm/
│   └── instamini/
│       ├── Chart.yaml         # Helm chart metadata
│       ├── values.yaml        # Default chart values
│       ├── deployment.yaml    # K8s Deployment for the Flask app
│       ├── service.yaml       # K8s Service (LoadBalancer)
│       └── ...
└── README.md                  # This README
```

<br />

## CI/CD Workflow

1. **Push or PR** triggers `ci-cd.yml`:
   - **Install** Terraform, gcloud, helm, vault CLI
   - **Test** Python code with `python -m unittest`
   - **Provision** or update GKE with Terraform
   - **Init** & **auto-unseal** Vault with GCP KMS
   - **Build & push** Docker image to Docker Hub
   - **Install** Argo CD & create an Argo CD App pointing to `helm/instamini`
2. **Argo CD** automatically syncs the Helm chart to your GKE cluster.
3. **App** is accessible via a GCP LoadBalancer Service.

<br />

## Setup & Usage

### 1. Prerequisites
- **Google Cloud** project, GCP service account JSON base64-encoded
- **GitHub Secrets**:
  - `GCP_SA_KEY` (base64 of JSON)
  - `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_ZONE`
  - `CLOUD_SQL_INSTANCE_NAME`
  - `VAULT_GCP_SA_KEY_B64`
  - `DB_ROOT_PASSWORD`, `DB_ADMIN_PASSWORD`
  - `DOCKERHUB_USERNAME`, `DOCKERHUB_PASSWORD`
  - Optional: `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASS`, etc.

### 2. Local Testing
```bash
# 1) Build Docker locally
"docker build -t yourusername/instamini:v1"

# 2) Run container locally
docker run -p 5001:5001 yourusername/instamini:v1

# 3) Access http://localhost:5001
```

### 3. Deploy via GitHub Actions
- **Push** to `main` branch.
- Pipeline provisions or updates GKE, Vault, etc.
- Argo CD automatically deploys your Helm chart.
- Check logs for external IP of your app.

<br />

## Terraform & Vault

- **Terraform** in `terraform/main.tf`:
  - Creates GKE cluster
  - Configures Vault with GCP KMS
  - Stores the Vault root token in Secret Manager
- **Vault** auto-initializes and unseals, ready to manage secrets if desired (static or dynamic).

<br />

## Helm & Argo CD

- **Helm Chart** in `helm/instamini/`:
  - `values.yaml` sets image repo/tag, service type, etc.
  - `deployment.yaml` references environment variables from K8s secrets.
- **Argo CD**:
  - Installed by the pipeline
  - Creates an app that watches your GitHub repo
  - Syncs Helm changes automatically to the GKE cluster

<br />

## Security Considerations

- **GitHub Secrets** store sensitive data (DB passwords, GCP keys).
- **Kubernetes Secrets** store DB credentials in `mysite-db-secrets`.
- **Vault** if you choose dynamic database credentials. Root token is in Google Secret Manager.
- **.gitignore** & others help avoid committing secrets to git.

<br />

## Future Enhancements

- **Monitoring**: Integrate Prometheus/Grafana for cluster/app metrics.
- **Workload Identity**: Connect GKE -> Vault more securely (no manual keys).
- **Cleanup**: Automated old Docker image cleanup or Helm chart revisions.

<br />

## Contributing

1. **Fork** this repo & branch off `main`.
2. **Commit** changes & create a pull request.
3. CI/CD pipeline runs automatically on PR merges.

<br />

## License

This project can be licensed under **MIT** or the license of your choice.

---

**Enjoy your fully automated pipeline** from code to production! If you have any questions or suggestions, feel free to open an issue or pull request.
