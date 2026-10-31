#!/usr/bin/env bash
# One-time, run by a project admin: creates keyless GitHub -> Google Cloud deploy access.
# Usage: scripts/setup_cicd.sh PROJECT_ID [GITHUB_REPO]
set -euo pipefail

PROJECT="${1:?Usage: scripts/setup_cicd.sh PROJECT_ID [owner/repo]}"
REPO="${2:-nasirtrekker/hormuz-pulse-ai-agent}"
POOL="github"
PROVIDER="github-provider"
DEPLOYER_NAME="hormuz-deployer"
DEPLOYER="${DEPLOYER_NAME}@${PROJECT}.iam.gserviceaccount.com"
RUNTIME="hormuz-pulse-runtime@${PROJECT}.iam.gserviceaccount.com"
NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"

gcloud iam workload-identity-pools create "$POOL" --project "$PROJECT" --location global \
  --display-name "GitHub Actions" 2>/dev/null || true

# Only tokens from this exact repository are accepted.
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --project "$PROJECT" \
  --location global --workload-identity-pool "$POOL" \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition "assertion.repository=='${REPO}'" 2>/dev/null || true

gcloud iam service-accounts create "$DEPLOYER_NAME" --project "$PROJECT" \
  --display-name "Hormuz Pulse CI deployer" 2>/dev/null || true

gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" --project "$PROJECT" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" >/dev/null

# Deploy-only roles: no IAM admin, no Vertex AI access.
for role in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer \
            roles/storage.admin roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:${DEPLOYER}" \
    --role "$role" --condition=None >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME" --project "$PROJECT" \
  --role roles/iam.serviceAccountUser --member "serviceAccount:${DEPLOYER}" >/dev/null

cat <<OUT

Add these as GitHub repository VARIABLES (Settings > Secrets and variables > Actions > Variables).
They are identifiers, not secrets:
  GCP_PROJECT                    ${PROJECT}
  GCP_REGION                     europe-west2
  GCP_DEPLOYER_SA                ${DEPLOYER}
  GCP_WORKLOAD_IDENTITY_PROVIDER projects/${NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}
  PUBLIC_SERVICE                 false   (true = anyone with the link can use the app)
OUT
