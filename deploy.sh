#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${1:?Usage: ./deploy.sh PROJECT_ID [REGION]}"
REGION="${2:-europe-west2}"
SERVICE="hormuz-pulse"
RUNTIME_SA_NAME="hormuz-pulse-runtime"
SECRET_NAME="google-api-key"
PUBLIC_SERVICE="${PUBLIC_SERVICE:-false}"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
ADK="$SCRIPT_DIR/.venv/bin/adk"

if [[ ! -x "$PYTHON" || ! -x "$ADK" ]]; then
  echo "Project environment is missing. Create .venv and install hormuz/requirements.txt first." >&2
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
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  secretmanager.googleapis.com \
  --project "$PROJECT"

RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
if [[ -z "$(gcloud iam service-accounts list \
  --project "$PROJECT" \
  --filter="email=$RUNTIME_SA" \
  --format='value(email)')" ]]; then
  gcloud iam service-accounts create "$RUNTIME_SA_NAME" \
    --project "$PROJECT" \
    --display-name="Hormuz Pulse Cloud Run runtime"
fi

SECRET_NAMES="$(gcloud secrets list --project "$PROJECT" --format='value(name)')"
if ! printf '%s\n' "$SECRET_NAMES" | grep -Eq "(^|/)${SECRET_NAME}$"; then
  read -r -s -p "Gemini API key (stored in Secret Manager): " API_KEY
  printf '\n'
  if [[ -z "$API_KEY" ]]; then
    echo "Gemini API key cannot be empty." >&2
    exit 1
  fi
  printf '%s' "$API_KEY" | gcloud secrets create "$SECRET_NAME" \
    --project "$PROJECT" \
    --data-file=-
  unset API_KEY
fi

SECRET_RESOURCE="$(gcloud secrets versions list "$SECRET_NAME" \
  --project "$PROJECT" \
  --filter='state=ENABLED' \
  --sort-by='~createTime' \
  --limit=1 \
  --format='value(name)')"
SECRET_VERSION="${SECRET_RESOURCE##*/}"
if [[ -z "$SECRET_VERSION" || "$SECRET_VERSION" == "$SECRET_RESOURCE" ]]; then
  echo "Secret $SECRET_NAME has no enabled versions." >&2
  exit 1
fi

gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --project "$PROJECT" \
  --member="serviceAccount:$RUNTIME_SA" \
  --role="roles/secretmanager.secretAccessor"

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
  --update-env-vars GOOGLE_GENAI_USE_VERTEXAI=FALSE \
  --update-secrets "GOOGLE_API_KEY=$SECRET_NAME:$SECRET_VERSION"

URL="$(gcloud run services describe "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --format='value(status.url)')"
printf 'Deployed service: %s\n' "$URL"
if [[ "$PUBLIC_SERVICE" == "false" ]]; then
  printf 'Access is authenticated. Grant Cloud Run Invoker to users who need access.\n'
fi
