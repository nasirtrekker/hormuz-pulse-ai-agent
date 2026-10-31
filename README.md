# Hormuz Pulse

A web app for forecasters, investors and the public. Open the link and you see
real Brent crude history, a data-driven 48-hour price range with its backtested
reliability, and a button that runs a team of Gemini research agents (Google ADK)
for a live briefing on the Strait of Hormuz.

**Live app: https://hormuz-pulse-2o4l63xkqq-nw.a.run.app**

![Dashboard](docs/dashboard.png)

## What you see and how to use it

| Dashboard section | What it does | Needs cloud? |
|---|---|---|
| Top numbers | Latest Brent close, 48-hour low (10th percentile), median, high (90th percentile), and the volatility used | no |
| Chart | Last 260 trading days with the shaded 80% range two trading days ahead | no |
| Try your own starting price | Type a price (10 to 500 USD) and click **Recalculate range**; the range is recomputed around it | no |
| How reliable is this range? | Walk-forward backtest: how often real outcomes fell inside the range | no |
| Does news predict the price? | Results of the model comparison, including the news index test | no |
| Live research briefing | Click **Generate briefing**. Agents search current news and sentiment, a critic checks the result, and the answer is screened by guardrails. Takes 1 to 3 minutes | yes (Vertex AI) |

Read the range as a risk band, not a price target: the backtest shows the band is
calibrated but no method beat "the price stays the same" for direction.

## How the app is built

```mermaid
flowchart LR
    User([Browser]) -->|HTTPS| CR
    subgraph GCP[Google Cloud, project hormuz-pulse-agent]
        CR[Cloud Run: FastAPI app<br/>app/main.py]
        CR --> RM[Range model<br/>stat_model.py, backtest.py]
        CR --> AG[ADK agent team<br/>agent.py]
        AG --> GR[Guardrails<br/>guardrails.py]
        GR -.optional.-> MA[Model Armor<br/>model_armor.py]
        AG --> VX[Vertex AI Gemini<br/>+ Google Search grounding]
    end
    RM --> DS[(Public data snapshots<br/>FRED Brent, OVX, VIX, USD, GPR)]
    DS -. refreshed at most daily .-> FRED[FRED]
    GH[GitHub Actions] -->|keyless deploy, Workload Identity| CR
```

Cloud resources are billed only when someone uses the app: Cloud Run scales to
zero when idle, and Vertex AI is called only when a visitor clicks "Generate
briefing". The dashboard itself uses no paid model calls.

| Tool | Used for |
|---|---|
| FastAPI + uvicorn | web server, JSON API, static dashboard, rate limits, security headers |
| Google ADK | agent team (parallel research, predictor, critic) |
| Gemini on Vertex AI, Google Search grounding | news, sentiment and latest-price research |
| Model Armor (optional) | managed prompt-injection, sensitive-data and responsible-AI filters |
| FRED, Caldara-Iacoviello GPR | public data for the range model and experiments |
| scikit-learn (dev only) | the model comparison in `scripts/experiment.py` |
| Docker, Cloud Run, Cloud Build | container build and hosting |
| GitHub Actions, Workload Identity Federation | CI and keyless deploy |

## What each file does

| File | Job |
|---|---|
| `app/main.py` | FastAPI service: endpoints, data refresh and backtest cache, rate limiting |
| `app/static/index.html`, `app.js` | the dashboard (canvas chart and tables), no external libraries |
| `hormuz/market_data.py` | loads and refreshes Brent and OVX series from FRED |
| `hormuz/stat_model.py` | computes the 48-hour range from standardised historical returns |
| `hormuz/backtest.py` | walk-forward test of that range |
| `hormuz/agent.py` | ADK pipeline and the `historical_range_48h` tool |
| `hormuz/guardrails.py`, `hormuz/model_armor.py` | ingress, tool-output and egress checks |
| `scripts/experiment.py` | compares models and writes `hormuz/data/experiment_results.json` |
| `scripts/backtest.py` | prints the backtest as JSON |
| `deploy.sh`, `Dockerfile`, `scripts/setup_cicd.sh`, `.github/workflows/` | deployment and CI/CD |
| `tests/`, `evals/` | unit tests, attack-corpus regression, ADK eval |

API: `GET /api/history`, `GET /api/forecast?price=`, `GET /api/backtest`,
`GET /api/evidence`, `POST /api/brief`, `GET /healthz`. Public use is protected by
per-IP and daily briefing limits (`BRIEFS_PER_IP_PER_HOUR`, `BRIEFS_PER_DAY`, per
instance), the guardrails, a 300 s timeout and at most 3 instances.

## Quick start

```bash
uv venv --python 3.12 .venv && uv pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # set GOOGLE_CLOUD_PROJECT
.venv/bin/uvicorn app.main:app --port 8080     # open http://localhost:8080
.venv/bin/python -m unittest discover -s tests
```

The dashboard needs no credentials. For the briefing run
`gcloud auth application-default login` and enable `aiplatform.googleapis.com`.
Deploy with `PUBLIC_SERVICE=true ./deploy.sh <project-id>`.

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

## Guardrails (ingress, tool output, egress)

All checks live in `hormuz/guardrails.py` and run as ADK callbacks. They are
deterministic code, not prompts, and are covered by `tests/test_guardrails.py`.

| Checkpoint | Callback | What it does |
|---|---|---|
| Ingress | `before_agent_callback` on `root_agent` | Blocks empty, over-1000-character, control-character, attachment and instruction-override requests before any model call. |
| Research output (web search) | `after_agent_callback` on the 3 research agents | Treats web-derived text as untrusted: strips control characters, removes injection phrases and secrets, caps length, and appends `VALIDATION FAILED` if `RISK LEVEL`, `SENTIMENT SCORE` (-1..1) or `BRENT PRICE` (10..500) is missing or implausible. The predictor then refuses to compute. |
| Tool output | `after_tool_callback`, `on_tool_error_callback` on `predictor_agent` | Rejects non-finite or implausible results (forecast outside 50%..150% of the input price) and reports invalid inputs to the model instead of crashing the run. |
| Egress | `after_model_callback` on every LLM agent | Redacts API keys, tokens and private keys, caps output length, and appends the not-financial-advice disclaimer to the final briefing if missing. |

### Model Armor (managed layer)

`hormuz/model_armor.py` adds Google Cloud Model Armor on top of the code checks.
It is off until `MODEL_ARMOR_LOCATION` and the template names are set.

| Template | Applied to | Filters |
|---|---|---|
| `hormuz-input` | user prompt and untrusted web research | prompt injection and jailbreak (default `LOW_AND_ABOVE`), sensitive data (basic), malicious URLs, responsible AI (`MEDIUM_AND_ABOVE`) |
| `hormuz-output` | final briefing from the critic | sensitive data, malicious URLs, responsible AI |

Separate input and output templates keep each side tunable and traceable.
Confidence: `HIGH` = fewest false positives, `LOW_AND_ABOVE` = most detections
and most false positives. Tune with `MA_PI_CONFIDENCE` and `MA_RAI_CONFIDENCE`.
If the service errors the request is blocked; set `MODEL_ARMOR_FAIL_OPEN=true`
to allow it instead.

```bash
gcloud services enable modelarmor.googleapis.com --project hormuz-pulse-agent
.venv/bin/python scripts/create_model_armor_templates.py hormuz-pulse-agent us-central1
# then add to .env: MODEL_ARMOR_LOCATION=us-central1
#   MODEL_ARMOR_INPUT_TEMPLATE=hormuz-input  MODEL_ARMOR_OUTPUT_TEMPLATE=hormuz-output
```

The agent's own identity needs `roles/modelarmor.user` (`deploy.sh` grants it to
the Cloud Run service account); the script needs `roles/modelarmor.admin`.
Model Armor has not been exercised against the live service from this repo.

These are best-effort filters: pattern matching cannot catch every prompt
injection or leak, so keep the service private and the runtime service account
limited to `roles/aiplatform.user`.

Network ingress and egress: Cloud Run keeps its default ingress (`all`) but is
protected by IAM authentication unless you set `PUBLIC_SERVICE=true`. Egress is
left open because the agents must reach Vertex AI and Google Search; restricting
it needs a VPC with Cloud NAT and is not configured here.

## Prediction: data and method

All market data is public and no API key is needed.

| Item | Source | Used for |
|---|---|---|
| Brent daily spot, 1987 to 2026-10-27 (9,117 valid daily prices) | FRED `DCOILBRENTEU` | the 48-hour range |
| Crude oil implied volatility (OVX), from 2007 | FRED `OVXCLS` | the 48-hour range (options-market expectation) |
| VIX, USD index | FRED `VIXCLS`, `DTWEXBGS` | experiment features only |
| Geopolitical Risk Index, daily, from newspaper coverage, 1985 on | Caldara and Iacoviello (matteoiacoviello.com/gpr.htm) | experiment feature only |
| Latest price, news, sentiment | Gemini with Google Search grounding, at request time | the starting price and the narrative |

Snapshots are in `hormuz/data/`; refresh Brent and OVX with `python -m hormuz.market_data`.

## How the range is computed

`hormuz/stat_model.py` (`forecast_blend`): for each past day it takes the 2-day
log return divided by a volatility scale, where the scale is the geometric mean of
20-day realised volatility and the OVX-implied daily volatility. The 10th/50th/90th
percentiles of those standardised returns come from the data (no normal assumption)
and are scaled by today's blended volatility around the live price. The tool
`historical_range_48h` in `hormuz/agent.py` exposes it to the predictor agent.

## Evidence (`python scripts/experiment.py`, walk-forward, 1,503 unseen 2-day moves, 2015 to 2026)

Quantiles are refit yearly using only past data and scored with pinball loss.

| Model | Score vs realised-vol model | 80% range coverage |
|---|---|---|
| Realised-vol empirical | baseline | 79.4% |
| Implied-vol (OVX) empirical | -2.0% | 76.5% |
| Blend of both (used) | -1.7% | 78.6% |
| Gradient boosting, market features | -0.2% | n/a |
| Gradient boosting, market + news (GPR) | -0.2% | n/a |

Findings, stated plainly:
- The range is calibrated, and options-implied volatility improves it modestly.
- The Geopolitical Risk news index showed no predictive power for the next 2-day
  direction (Spearman p between 0.36 and 0.98) and added nothing in the models.
- No model beat "price stays the same" as a point forecast. The app therefore shows a
  range, not a price target.
- LLM sentiment cannot be backtested historically, so it is a labelled narrative and
  scenario input (`predict_48h`), not a validated predictor.
- A crisis can move prices beyond anything in the history.

The same results are served at `/api/evidence` and shown in the dashboard.

## Agent pipeline and guardrails

```mermaid
flowchart TD
    U[User] --> I[Ingress guard + Model Armor input template]
    I -->|blocked| X[Refusal message]
    I --> P
    subgraph P[ParallelAgent: research with Google Search]
        N[news] ~~~ S[sentiment] ~~~ M[market data]
    end
    P --> RG[Research guard: sanitize, range-check, Model Armor input template]
    RG --> PR[Predictor: data-driven range tool + labelled scenario overlay, tool-output guard]
    PR --> C[Critic]
    C --> E[Egress guard: redact, disclaimer, Model Armor output template]
    E --> O[Briefing]
```

The pipeline is an ADK `SequentialAgent` in `hormuz/agent.py`. Agents share state
through `output_key` and `{placeholders}`. The range comes from the statistical model above; the scenario overlay is a
labelled fixed formula, not an ML model. Gemini only researches and critiques.

## CI/CD and operations

| Stage | Trigger | What runs |
|---|---|---|
| CI | every pull request and push to `main` | unit tests, attack-corpus regression (`tests/data/attack_corpus.json`), `adk eval` on `evals/guardrails.evalset.json`, secret scan (gitleaks), script syntax checks. No cloud credentials, no model calls. |
| CD | CI passes on `main`, or manual run | keyless Workload Identity Federation, then `deploy.sh` with `SKIP_BOOTSTRAP=true`, then a check that the new revision is ready. Uses the `production` GitHub environment, so you can require manual approval there. |
| Dependencies | weekly | Dependabot for pip and GitHub Actions. |

Separation of duties: the CI deployer service account can deploy Cloud Run
revisions but cannot change IAM or call Vertex AI. The runtime service account
can call Vertex AI and Model Armor but cannot deploy. Admin setup (APIs, runtime
account, Model Armor templates) is a one-time manual step.

One-time setup by a project admin:

```bash
./deploy.sh hormuz-pulse-agent                      # APIs, runtime service account, first deploy
scripts/setup_cicd.sh hormuz-pulse-agent            # keyless GitHub access; prints the variables to set
```

Then add the printed values as GitHub repository variables (no secrets are
needed) and, optionally, add required reviewers to the `production` environment.

Guardrail changes must keep the attack corpus green: add every new attack string
to `tests/data/attack_corpus.json` so it can never regress. Live-model
evaluation (`adk eval` with real forecasts) calls Vertex AI and costs money, so
it is deliberately not part of CI.

Rollback: Cloud Run keeps previous revisions.

```bash
gcloud run revisions list --service hormuz-pulse --region europe-west2
gcloud run services update-traffic hormuz-pulse --region europe-west2 --to-revisions REVISION=100
```

Logs: `gcloud run services logs read hormuz-pulse --region europe-west2`. Model
Armor blocks are logged with the filter names only, never the user's text.

## Local development

Requirements: Python 3.10 or later, [uv](https://docs.astral.sh/uv/getting-started/installation/),
the Google Cloud CLI, and a Google Cloud project with billing enabled. The app
calls Gemini through Vertex AI using your Google identity, so there is no API
key to store.

```bash
export PATH="$HOME/.local/bin:$PATH"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env        # .env is gitignored; edit the project ID if needed
gcloud auth application-default login
set -a; source .env; set +a
.venv/bin/uvicorn app.main:app --port 8080     # the web app: http://localhost:8080
```

The dashboard works immediately. The briefing button needs the Vertex AI login
above. For the ADK developer UI run `.venv/bin/adk web` and pick `hormuz`.

Run the tests (API, guardrails, model, attack corpus):

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -W ignore -m unittest discover -s tests
```

## Google Cloud Run deployment

Deployment builds the repository `Dockerfile` with Cloud Build (`gcloud run deploy --source`).
The service is private by default, uses a dedicated Cloud Run service account,
and calls Gemini through Vertex AI with that service account (`roles/aiplatform.user`).
No API key is created, stored, or committed.

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
   use the runtime service account, grant IAM roles, and run Cloud Build.
   Ask the project administrator to grant the least-privilege roles required
   by the organization. Cloud Build builds the container; its build
   service account must have `roles/cloudbuild.builds.builder`.

### Deploy

From the project directory:

```bash
./deploy.sh YOUR_PROJECT_ID [REGION]
```

The default region is `europe-west2`; Vertex AI uses the `global` location
(override with `VERTEX_LOCATION`). Cloud Run requires authentication by default.
To make the service public for a demo, explicitly opt in:

```bash
PUBLIC_SERVICE=true ./deploy.sh YOUR_PROJECT_ID [REGION]
```

Public access can incur Vertex AI costs, billed to the project: each briefing runs
several Gemini calls with Google Search. The limits above cap this but are per
instance. Briefing sessions are in memory and deleted after each request.
Model Armor is optional: create the templates first, then deploy with
`ENABLE_MODEL_ARMOR=true`, otherwise it fails closed and blocks every request.

## Production limitations and next steps

The 48-hour range is backtested, but the price series is daily spot data that
FRED updates with a delay, and the briefing's live price comes from web search,
not a licensed feed. Before using this for financial decisions: add a licensed
real-time price source, test whether news-volume features (for example GDELT)
improve the backtest, and monitor calibration over time. Limits are per instance;
use Cloud Armor or a shared store if abuse becomes a problem.

For incidents and request diagnostics, inspect the Cloud Run service logs in
Cloud Logging.
