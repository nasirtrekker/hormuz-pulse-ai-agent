# Hormuz Pulse

Hormuz Pulse is a Google Agent Development Kit (ADK) demo that researches Strait
of Hormuz and oil-market news and produces an illustrative Brent price scenario
for the next 48 hours.

## What makes the scenario

The project does **not** contain a trained or backtested machine-learning model.
`gemini-3.8-flash` (override with the `GEMINI_MODEL` environment variable) is the language model used by the research, input-extraction,
and critic agents; the Brent scenario is calculated by the deterministic Python
function `predict_48h` in `hormuz/agent.py`. It combines fixed momentum,
sentiment, and Hormuz-risk weights. The displayed range comes from a fixed
volatility assumption and is **not** a calibrated 80% confidence interval.
These assumptions have not been validated against historical Brent data. Treat
the result as an educational scenario, not trading advice.

## Multi-agent workflow

The ADK harness is defined in `hormuz/agent.py`:

1. `collect` is a `ParallelAgent`: `news_agent`, `sentiment_agent`, and
   `data_agent` each use Gemini and Google Search; their results are saved under
   the `news`, `sentiment`, and `market` session-state keys.
2. `predictor_agent` runs after collection. Gemini extracts the inputs and calls
   the Python `predict_48h` tool for the base and high-risk scenarios.
3. `critic_agent` runs last, checks the inputs and scenario, and writes the
   briefing.
4. `root_agent` is a `SequentialAgent` that coordinates those three stages.

Gemini does not train a model or independently validate the numerical formula.
The agents depend on search results and language-model extraction, so inputs
can be incomplete or wrong. Missing or invalid numerical inputs are rejected by
the Python tool rather than silently converted into a forecast.

## Local development

Requirements: Python 3.10 or later, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).

Create the environment and install the pinned direct dependency:

```bash
export PATH="$HOME/.local/bin:$PATH"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r hormuz/requirements.txt
```

Copy `.env.example` to `.env` in the project root and put your key in it. `.env`
is gitignored. Keep it in the project root, not in `hormuz/`, because
`adk deploy` uploads the agent folder. Then load it and launch the ADK
development UI:

```bash
set -a; source .env; set +a
.venv/bin/adk web
```

Select `hormuz` in the UI and ask: “Predict Brent for the next 48 hours.”
Do not commit API keys or put them in command-line arguments.

Run the deterministic forecast tests:

```bash
.venv/bin/python -m unittest discover -s tests
```

## Google Cloud Run deployment

Deployment uses Google Cloud Build and the ADK `adk deploy cloud_run` command.
The service is private by default, uses a dedicated Cloud Run service account,
and reads the Gemini API key from Secret Manager. The API key is never written
to the source tree or passed as a command-line argument.

### One-time prerequisites

1. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
   If installed under `~/google-cloud-sdk`, add it to the current shell:

   ```bash
   export PATH="$HOME/google-cloud-sdk/bin:$PATH"
   export CLOUDSDK_PYTHON="$(pwd)/.venv/bin/python"
   ```

2. Use a Google Cloud project with billing enabled. Sign in and select the
   project:

   ```bash
   export CLOUDSDK_PYTHON="$(pwd)/.venv/bin/python"
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

3. The deploying identity needs permission to enable APIs, deploy Cloud Run,
   use the runtime service account, create/read secrets, and run Cloud Build.
   Ask the project administrator to grant the least-privilege roles required
   by the organization. ADK's Cloud Run deploy uses Cloud Build; its build
   service account must have `roles/cloudbuild.builds.builder`.

### Deploy

From the project directory:

```bash
./deploy.sh YOUR_PROJECT_ID [REGION]
```

The default region is `europe-west2`. On first deployment, the script prompts
for the Gemini API key and stores it in Secret Manager. Subsequent deployments
reuse that secret version. The deployment pins the enabled Secret Manager version to the Cloud Run
revision. Cloud Run requires authentication by default. To make the service
public for a demo, explicitly opt in:

```bash
PUBLIC_SERVICE=true ./deploy.sh YOUR_PROJECT_ID [REGION]
```

Public access can incur usage and model costs; use it only when intended. The
ADK web UI is a development interface, not a hardened public product UI.
Cloud Run's default ADK session and artifact services are in-memory, so session
data does not persist across instance restarts or scale-down.

## Production limitations and next steps

Before using this for operational or financial decisions, replace the heuristic
with a model trained and evaluated on timestamped historical Brent, market, and
Hormuz-risk data. Use a chronological holdout/backtest, compare with simple
baselines, document prediction error and calibration, and monitor drift. Use
reliable licensed market data instead of treating web search as a price feed.
Also add durable session storage only if the product needs conversation history.

For incidents and request diagnostics, inspect the Cloud Run service logs in
Cloud Logging. Rotate the key by adding a new Secret Manager version; do not
copy it into source code or a deployment command.
