# DevOps Final Project: CI/CD with Terraform, Vault, Helm, Argo CD & GitHub Actions

## Overview

This project automates:

1. **Continuous Integration**: Build & test in GitHub Actions  
2. **Continuous Deployment**: Deploy to GKE via Terraform, Helm, and Argo CD  
3. **Vault Secrets**: Automatic Vault init/unseal with GCP KMS  
4. **Docker**: Containerize and push to Docker Hub  

## Repository Structure

```
.github/workflows/ci-cd.yml      # GitHub Actions pipeline
terraform/
  main.tf, variables.tf, ...     # Terraform for GKE, Vault, GCP KMS
  vault-values.yaml              # Vault Helm override
  vault-admin-sa.yaml            # Vault service account
  vault-init-job.yaml            # One-time job to configure Kubernetes auth
helm/instamini/
  Chart.yaml, values.yaml        # Helm chart for your Flask app
  deployment.yaml, service.yaml  # K8s manifests for your app
Dockerfile                       # Docker build for the Flask app
requirements.txt                 # Python dependencies
app.py, tests/                  # Flask application & tests
```

## Key Steps

1. **GitHub Actions** (`ci-cd.yml`):  
   - Installs Terraform, gcloud, helm, vault.  
   - Provisions GKE, sets up Vault, builds & pushes Docker image.  
   - Deploys via Argo CD automatically.  

2. **Terraform** (`main.tf`, etc.):  
   - Creates GKE cluster, Vault with GCP KMS.  
   - Stores Vault Root Token in Secret Manager.  
   - Configures Cloud SQL references.  

3. **Vault**:  
   - Auto-initialized/unsealed.  
   - `vault-init-job.yaml` sets up Kubernetes auth (`disable_iss_validation=true` on GKE).  

4. **Helm + Argo CD**:  
   - Helm chart in `helm/instamini/`.  
   - Argo CD syncs your chart on every commit.  

5. **MySQL**:  
   - Credentials injected by Vault sidecar (dynamic) or fallback static secrets.  

---

## Final Code

### 1) GitHub Actions: `.github/workflows/ci-cd.yml`
<details>
<summary>Click to show</summary>

```yaml
name: DevOps Final Project

on:
  push:
    branches: [ "main" ]
  pull_request:
    branches: [ "main" ]
  workflow_dispatch:

jobs:
  build-test-deploy:
    runs-on: ubuntu-latest
    env:
      GOOGLE_APPLICATION_CREDENTIALS: ${{ github.workspace }}/gcp-key.json
      TF_VAR_vault_gcp_sa_key_b64: ${{ secrets.VAULT_GCP_SA_KEY_B64 }}
      TF_VAR_db_root_password: ${{ secrets.DB_ROOT_PASSWORD }}
      TF_VAR_db_admin_password: ${{ secrets.DB_ADMIN_PASSWORD }}

    steps:
      - name: Check out repository
        uses: actions/checkout@v3

      - name: List repository
        run: |
          ls -la
          ls -la terraform || true

      - name: Install Dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y apt-transport-https ca-certificates gnupg curl jq unzip python3-pip
          # Terraform
          TF_VERSION="1.4.6"
          curl -sSL "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip" -o terraform.zip
          unzip -o terraform.zip -d tfbin
          sudo mv tfbin/terraform /usr/local/bin/terraform
          terraform -version

          # GCloud CLI, kubectl, helm, vault CLI, python
          ...
          pip3 install -r requirements.txt || true

      - name: GCP Auth
        run: |
          echo '${{ secrets.GCP_SA_KEY }}' | base64 --decode > $GITHUB_WORKSPACE/gcp-key.json
          gcloud auth activate-service-account --key-file=$GITHUB_WORKSPACE/gcp-key.json
          gcloud config set project ${{ secrets.GCP_PROJECT_ID }}

      - name: Terraform Import optional KMS resources
        run: |
          cd terraform
          terraform init -backend-config="bucket=tfstate-bucket-instamini" -backend-config="prefix=terraform/state"
          terraform import google_kms_key_ring.vault_ring "projects/${{ secrets.GCP_PROJECT_ID }}/locations/${{ secrets.GCP_REGION }}/keyRings/${{ secrets.KMS_KEY_RING }}" || true
          terraform import google_kms_crypto_key.vault_key ...
          terraform import google_secret_manager_secret.root_token_secret ...

      - name: Test Python App
        env:
          MYSQL_HOST: ${{ secrets.MYSQL_HOST }}
          MYSQL_PORT: ${{ secrets.MYSQL_PORT }}
          MYSQL_USER: ${{ secrets.MYSQL_USER }}
          MYSQL_PASS: ${{ secrets.MYSQL_PASS }}
          MYSQL_DB:   ${{ secrets.MYSQL_DB }}
        run: |
          python3 -m unittest discover -s tests

      - name: Check if cluster exists
        id: clustercheck
        run: |
          EXISTING=$(gcloud container clusters list --filter="name=vault-ha-cluster" --format="value(name)" || true)
          if [ "$EXISTING" == "vault-ha-cluster" ]; then
            echo "cluster_exists=true" >> $GITHUB_OUTPUT
          else
            echo "cluster_exists=false" >> $GITHUB_OUTPUT
          fi

      - name: Terraform Apply (Create Cluster)
        if: steps.clustercheck.outputs.cluster_exists == 'false'
        env:
          TF_VAR_gcp_project: ${{ secrets.GCP_PROJECT_ID }}
          ...
        run: |
          cd terraform
          terraform apply -target=google_container_cluster.primary -target=google_container_node_pool.primary_nodes -auto-approve

      - name: Configure Kubeconfig
        run: |
          gcloud container clusters get-credentials vault-ha-cluster --zone ${{ secrets.GCP_ZONE }} --project ${{ secrets.GCP_PROJECT_ID }}

      - name: Terraform Apply (KMS + Vault)
        env:
          TF_VAR_gcp_project: ${{ secrets.GCP_PROJECT_ID }}
          ...
        run: |
          cd terraform
          terraform apply -auto-approve

      - name: Create MySQL Secret
        run: |
          gcloud container clusters get-credentials vault-ha-cluster --zone ${{ secrets.GCP_ZONE }} --project ${{ secrets.GCP_PROJECT_ID }}
          kubectl delete secret mysite-db-secrets -n default --ignore-not-found=true
          kubectl create secret generic mysite-db-secrets -n default \
            --from-literal=MYSQL_HOST=${{ secrets.MYSQL_HOST }} \
            --from-literal=MYSQL_PORT=${{ secrets.MYSQL_PORT }} \
            --from-literal=MYSQL_USER=${{ secrets.MYSQL_USER }} \
            --from-literal=MYSQL_PASS=${{ secrets.MYSQL_PASS }} \
            --from-literal=MYSQL_DB=${{ secrets.MYSQL_DB }}

      - name: Login to DockerHub
        uses: docker/login-action@v2
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_PASSWORD }}

      - name: Docker Build & Push
        uses: docker/build-push-action@v2
        with:
          context: .
          file: ./Dockerfile
          push: true
          tags: ${{ secrets.DOCKERHUB_USERNAME }}/instamini:v3
          platforms: linux/amd64

      - name: Configure kubectl (again)
        run: |
          gcloud container clusters get-credentials vault-ha-cluster --zone ${{ secrets.GCP_ZONE }} --project ${{ secrets.GCP_PROJECT_ID }}

      - name: Install Argo CD & Create App
        run: |
          kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
          kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
          ...
          argocd app create instamini \
            --repo https://github.com/casual-cat/instamini-project.git \
            --path helm/instamini \
            ...
            --helm-set image.repository=${{ secrets.DOCKERHUB_USERNAME }}/instamini \
            --helm-set image.tag=v3 \
            --upsert

      - name: Print External IP
        run: |
          for i in {1..10}; do
            EXTERNAL_IP=$(kubectl get svc instamini-mysite -n default -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
            ...
```
</details>

### 2) Terraform & Vault (short highlights)

<details>
<summary>Click to show <code>main.tf</code> & <code>vault-init-job.yaml</code></summary>

```hcl
# main.tf
resource "google_container_cluster" "primary" { ... }
resource "google_container_node_pool" "primary_nodes" { ... }

resource "helm_release" "vault" {
  chart = "vault"
  values = [ file("${path.module}/vault-values.yaml") ]
  ...
}

resource "null_resource" "vault_init_and_config" {
  provisioner "local-exec" {
    command = <<-EOT
      # 1) Wait for Vault LB
      # 2) operator init (if not already done)
      # 3) store root token in Secret Manager
      # 4) enable database secrets & config
      ...
    EOT
  }
}

# vault-init-job.yaml
vault write auth/kubernetes/config \
  token_reviewer_jwt="$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" \
  kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
  kubernetes_host="https://kubernetes.default.svc.cluster.local:443" \
  disable_iss_validation=true  # GKE fix
```
</details>

### 3) Helm Chart for App

<details>
<summary>Click to show short snippets</summary>

```yaml
# deployment.yaml (Helm)
...
annotations:
  vault.hashicorp.com/agent-inject: "true"
  vault.hashicorp.com/agent-inject-secret-dbcreds: "database/creds/instamini"
...
env:
- name: MYSQL_HOST
  valueFrom:
    secretKeyRef:
      name: mysite-db-secrets
      key: MYSQL_HOST
...
```
</details>

---

## Usage

- Set GitHub Secrets (`GCP_SA_KEY`, `GCP_PROJECT_ID`, `VAULT_GCP_SA_KEY_B64`, `DB_ROOT_PASSWORD`, `DB_ADMIN_PASSWORD`, `DOCKERHUB_USERNAME`, `DOCKERHUB_PASSWORD`, etc.).  
- Commit & push to `main`.  
- GitHub Actions runs Terraform, configures Vault, builds/pushes Docker, deploys via Argo CD.  
- Check the Argo CD and app’s external IP from CI logs.  

## Troubleshooting

- **403 from Vault**: Ensure `disable_iss_validation=true` in the `vault-init-job.yaml`.  
- **MySQL Access**: Confirm the user `db_admin` exists or rely on dynamic Vault creds.  
- **Argo CD IP**: Wait a few minutes for LB provisioning.

**Enjoy your fully automated pipeline!**