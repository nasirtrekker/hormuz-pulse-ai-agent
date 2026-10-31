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
ADK="$SCRIPT_DIR/.venv/bin/adk"

# Local runs use the project venv; CI uses the tools already on PATH.
if [[ ! -x "$PYTHON" || ! -x "$ADK" ]]; then
  PYTHON="$(command -v python3 || true)"
  ADK="$(command -v adk || true)"
fi
if [[ -z "$PYTHON" || -z "$ADK" ]]; then
  echo "adk not found. Create .venv and install hormuz/requirements.txt first." >&2
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

"$ADK" deploy cloud_run \
  --project "$PROJECT" \
  --region "$REGION" \
  --service_name "$SERVICE" \
  --app_name hormuz \
  --with_ui \
  "$SCRIPT_DIR/hormuz" \
  -- \
  --service-account="$RUNTIME_SA" \
  --max=3 \
  "${ACCESS_FLAGS[@]}"

gcloud run services update "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --update-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$VERTEX_LOCATION,MODEL_ARMOR_LOCATION=$MODEL_ARMOR_LOCATION,MODEL_ARMOR_INPUT_TEMPLATE=hormuz-input,MODEL_ARMOR_OUTPUT_TEMPLATE=hormuz-output"

URL="$(gcloud run services describe "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --format='value(status.url)')"
printf 'Deployed service: %s\n' "$URL"
if [[ "$PUBLIC_SERVICE" == "false" ]]; then
  printf 'Access is authenticated. Grant Cloud Run Invoker to users who need access.\n'
fi
