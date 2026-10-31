#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${1:?Usage: ./deploy.sh PROJECT_ID [REGION]}"
REGION="${2:-europe-west2}"
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"
MODEL_ARMOR_LOCATION="${MODEL_ARMOR_LOCATION:-us-central1}"
SERVICE="hormuz-pulse"
RUNTIME_SA_NAME="hormuz-pulse-runtime"
PUBLIC_SERVICE="${PUBLIC_SERVICE:-false}"
SKIP_BOOTSTRAP="${SKIP_BOOTSTRAP:-false}"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

# gcloud needs a Python 3.10+; prefer the project venv, then whatever python3 is on PATH.
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
  echo "python3 not found." >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  if [[ -x "$HOME/google-cloud-sdk/bin/gcloud" ]]; then
    PATH="$HOME/google-cloud-sdk/bin:$PATH"
    export PATH
  else
    echo "Google Cloud CLI not found. Install it and add gcloud to PATH." >&2
    exit 1
  fi
fi

export CLOUDSDK_PYTHON="$PYTHON"

case "$PUBLIC_SERVICE" in
  true|false) ;;
  *)
    echo "PUBLIC_SERVICE must be either true or false." >&2
    exit 1
    ;;
esac

if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .; then
  echo "No active Google Cloud account. Run 'gcloud auth login' first." >&2
  exit 1
fi

gcloud config set project "$PROJECT"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

# One-time admin setup. CI sets SKIP_BOOTSTRAP=true because its deployer
# identity deliberately has no permission to change IAM or enable APIs.
if [[ "$SKIP_BOOTSTRAP" != "true" ]]; then
  gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    iam.googleapis.com \
    aiplatform.googleapis.com \
    modelarmor.googleapis.com \
    --project "$PROJECT"

  if [[ -z "$(gcloud iam service-accounts list \
    --project "$PROJECT" \
    --filter="email=$RUNTIME_SA" \
    --format='value(email)')" ]]; then
    gcloud iam service-accounts create "$RUNTIME_SA_NAME" \
      --project "$PROJECT" \
      --display-name="Hormuz Pulse Cloud Run runtime"
  fi

  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$RUNTIME_SA" \
    --role="roles/aiplatform.user" \
    --condition=None >/dev/null

  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$RUNTIME_SA" \
    --role="roles/modelarmor.user" \
    --condition=None >/dev/null

fi

if [[ "$PUBLIC_SERVICE" == "true" ]]; then
  ACCESS_FLAGS=(--allow-unauthenticated)
else
  ACCESS_FLAGS=(--no-allow-unauthenticated)
fi

ENV_VARS="GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$VERTEX_LOCATION"
# Only enable once scripts/create_model_armor_templates.py has created the templates:
# with missing templates the filter fails closed and blocks every request.
if [[ "${ENABLE_MODEL_ARMOR:-false}" == "true" ]]; then
  ENV_VARS+=",MODEL_ARMOR_LOCATION=$MODEL_ARMOR_LOCATION,MODEL_ARMOR_INPUT_TEMPLATE=hormuz-input,MODEL_ARMOR_OUTPUT_TEMPLATE=hormuz-output"
fi

# Builds the Dockerfile in this directory with Cloud Build and deploys it.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --source "$SCRIPT_DIR" \
  --service-account "$RUNTIME_SA" \
  --max-instances 3 \
  --concurrency 20 \
  --timeout 300 \
  --memory 1Gi \
  --set-env-vars "$ENV_VARS" \
  "${ACCESS_FLAGS[@]}"

URL="$(gcloud run services describe "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --format='value(status.url)')"
printf 'Deployed service: %s\n' "$URL"
if [[ "$PUBLIC_SERVICE" == "false" ]]; then
  printf 'Open the URL above. Access is authenticated. Grant Cloud Run Invoker to users who need access.\n'
fi
