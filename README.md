# Repo Analysis Microservice (GCP)

Pluggable GitHub repo analysis for hackathon submissions.

| Layer | Service |
|---|---|
| API | FastAPI on **Cloud Run** (service) |
| Worker | **Cloud Run Job** (`python -m app.worker`) |
| Jobs | **Firestore** (Firebase Admin) |
| LLM | **Vertex AI Gemini** |

The caller selects which metrics to run per request — the service never hardcodes “always check everything.”

## Architecture

```
Client
  → FastAPI Cloud Run Service
      → validate request
      → create Firestore job (queued)
      → trigger Cloud Run Job
      → return job_id
  → GET /analyze/{job_id}  (poll)

Cloud Run Job (same image, python -m app.worker)
  → read Firestore job
  → claim running
  → analyze repository
  → write result
  → succeeded | failed
  → exit
```

One container, two entrypoints:

| Role | Command |
|---|---|
| API | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Worker | `python -m app.worker` (`ANALYSIS_JOB_ID` set per execution) |

Job status is always one of `queued`, `running`, `succeeded`, `failed`. Worker retries reuse the same Cloud Run execution id and **never overwrite** a `succeeded` or `failed` report.

Locally, `CLOUD_RUN_JOB_NAME` is unset, so `POST /analyze` runs the worker **inline** on a FastAPI background task (same Firestore job document).

Static analysis remains the only engine that touches third-party repositories. **Runtime execution of submission code is not implemented.** See [Runtime sandbox threat model](docs/runtime-sandbox-security.md). Do not run untrusted repos until that design is reviewed and GCP isolation is in place.

## Quick start (local)

Requires [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
cp .env.example .env
# fill Firebase SA fields, GOOGLE_CLOUD_PROJECT, GITHUB_TOKEN
# Local: Firebase private key in .env is enough (no gcloud ADC required)
# Or: gcloud auth application-default login

uv sync          # create .venv + install deps
uv run python -m app
# or: uv run github-analyser
```

Run tests: `uv run pytest`

Binds to `0.0.0.0:$PORT` (default `8000`).

Jobs persist in Firestore collection `githubanalysis_jobs` (created on first write).
Static metrics work without Vertex; LLM halves need `GOOGLE_CLOUD_PROJECT` + Vertex AI API enabled.

## GCP setup

```bash
export PROJECT_ID=your-gcp-project
export REGION=us-central1

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  artifactregistry.googleapis.com \
  --project="$PROJECT_ID"

# Native Firestore (Firebase) — create once in console or:
gcloud firestore databases create --location="$REGION" --project="$PROJECT_ID"
```

### IAM (Cloud Run runtime SA)

Grant the Cloud Run service account:

- `roles/aiplatform.user` — Vertex AI Gemini
- `roles/datastore.user` — Firestore
- `roles/run.invoker` on the **worker job** (`github-analyser-worker`) — API triggers executions

## Deploy (Cloud Build → Cloud Run)

### 1. One-time GCP setup

```bash
export PROJECT_ID=your-gcp-project
export REGION=us-central1

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project="$PROJECT_ID"

# Firestore (once per project)
gcloud firestore databases create --location="$REGION" --project="$PROJECT_ID" 2>/dev/null || true

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

# Cloud Build SA — deploy to Cloud Run + push images
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${CB_SA}" --role="$ROLE" --quiet
done
```

**Secrets** (already in Secret Manager): `FIREBASE_PROJECT_ID`, `FIREBASE_PRIVATE_KEY`, `FIREBASE_CLIENT_EMAIL`, `FIREBASE_DATABASE_URL`, `FIREBASE_WEB_API_KEY`, `GITHUB_TOKEN`.

**Plain env vars** (Cloud Build substitutions in `cloudbuild.yaml`): Vertex AI settings.

### 2. Deploy with Cloud Build

```bash
gcloud builds submit --config cloudbuild.yaml --project=nxt-acad-hackathon
```

The final deploy step prints the **Cloud Run URL**. It also deploys Cloud Run Job `github-analyser-worker` from the **same image**. Vertex AI settings are baked into `cloudbuild.yaml` substitutions; override only if needed:

```bash
gcloud builds submit --config cloudbuild.yaml \
  --substitutions=_GEMINI_MODEL=gemini-2.0-flash \
  --project=nxt-acad-hackathon
```

**Substitutions** (optional overrides):

| Var | Default | Purpose |
|---|---|---|
| `_REGION` | `us-central1` | Cloud Run + Vertex region |
| `_ENVIRONMENT` | `production` | `ENVIRONMENT` env var |
| `_GCP_LOCATION` | `us-central1` | `GOOGLE_CLOUD_LOCATION` |
| `_GEMINI_MODEL` | `gemini-2.5-flash` | Vertex Gemini model |
| `_FIRESTORE_COLLECTION` | `github_analysis_jobs` | Firestore jobs collection |
| `_WORKER_JOB` | `github-analyser-worker` | Cloud Run Job name |
| `_RUN_SERVICE_ACCOUNT` | `nxt-acad-ai-hackathon-evaluate@nxt-acad-hackathon.iam.gserviceaccount.com` | Cloud Run runtime SA |

**Notes:**
- Cloud Run uses `_RUN_SERVICE_ACCOUNT` (not the default compute SA) so Secret Manager, Vertex, and Firestore permissions apply.
- Firebase + `GITHUB_TOKEN` mounted via `--update-secrets` (your existing Secret Manager names).
- Vertex AI uses the **Cloud Run runtime SA** (ADC), not the Firebase Admin key.
- Image tagged with `$BUILD_ID` (deploy), `$SHORT_SHA` (git triggers), and `latest`.
- API service and worker job share that image. The job command is `python -m app.worker`; the service stays `uvicorn`.

### Manual deploy (alternative)

```bash
gcloud run deploy github-analyser \
  --source . \
  --region us-central1 \
  --service-account nxt-acad-ai-hackathon-evaluate@nxt-acad-hackathon.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=nxt-acad-hackathon,GOOGLE_CLOUD_LOCATION=us-central1,GEMINI_MODEL=gemini-2.5-flash,FIRESTORE_COLLECTION_JOBS=github_analysis_jobs" \
  --set-secrets "GITHUB_TOKEN=GITHUB_TOKEN:latest,FIREBASE_PROJECT_ID=FIREBASE_PROJECT_ID:latest,FIREBASE_PRIVATE_KEY=FIREBASE_PRIVATE_KEY:latest,FIREBASE_CLIENT_EMAIL=FIREBASE_CLIENT_EMAIL:latest,FIREBASE_DATABASE_URL=FIREBASE_DATABASE_URL:latest,FIREBASE_WEB_API_KEY=FIREBASE_WEB_API_KEY:latest" \
  --memory 1Gi \
  --timeout 300 \
  --project nxt-acad-hackathon

# Worker job (same image). After `docker build` / Cloud Build:
# gcloud run jobs deploy github-analyser-worker \
#   --image IMAGE --region us-central1 \
#   --command /app/.venv/bin/python --args=-m,app.worker \
#   --task-timeout=1200 --max-retries=2 \
#   --set-env-vars APP_ROLE=worker,...
```

Then set `CLOUD_RUN_JOB_NAME=github-analyser-worker` on the API service.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /analyze` | Validate, create Firestore job (`queued`), start worker, return `job_id` |
| `POST /analyze/batch` | Same, many jobs |
| `GET /analyze/{job_id}` | Poll status + result (Firestore). Status: `queued` \| `running` \| `succeeded` \| `failed` |
| `GET /analyze/{job_id}?wait_seconds=60` | Poll with long-wait (blocks up to 120s) |
| `POST /analyze/sync` | **In-process** submit + wait (see below) |
| `GET /metrics` | Metric catalogue + schemas |
| `GET /rubrics` | Default rubric weights + max total score |
| `GET /health` | Liveness (`worker_mode` is `cloud_run_job` or `inline`) |

### `POST /analyze/sync`

Kept for compatibility. It creates a Firestore job and runs analysis **inside the API container**, then returns the finished `JobResponse`.

It does **not** use the Cloud Run Job worker. The Cloud Run *service* request timeout (currently 300s) still applies. Prefer `POST /analyze` + `GET /analyze/{job_id}` in production.

### Public repo gate

Every analysis starts with GitHub URL validation and a public-repo check:

- **Invalid URL** → HTTP 400 at submit time (before a job is created).
- **Private or inaccessible repo** → job completes with `status=succeeded`, `scoring.total_score=0`, and `access.reason` set (no GitHub prefetch, no Gemini).

Result payload includes:

```json
{
  "access": { "valid_url": true, "is_public": true, "reason": null },
  "scoring": { "total_score": 72.5, "max_total_score": 100, "rubrics": [...] },
  "metrics": { ... }
}
```

Rubric weights default from `RUBRIC_WEIGHTS_JSON` in `.env`. Override per request with `context.scoring.rubrics` or `options.scoring.rubrics`.

### Example request (hackathon evaluation)

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "github_url": "https://github.com/owner/repo",
    "context": {
      "provided_context": "This project is a multi-agent LangGraph study planner that uses RAG over course materials and Gemini to help students build personalized study schedules.",
      "rubrics": ["Uses an LLM", "Has real agent orchestration", "Full-stack demo"]
    }
  }'
```

| Rubric | Weight | Out of 20 |
|---|---|---|
| Full-stack demo | 50% | 10 |
| Uses an LLM | 20% | 4 |
| Real agent orchestration | 20% | 4 |
| Context fit & README | 10% | 2 |

Full-stack partial credit: frontend-only or backend-only earns **20%** of that rubric (2/20 total). Both stacks required for full 10/20.

`context.provided_context` is a plain paragraph describing the project. The repo is scored against it. All metrics always run. `track` is optional.

## Metrics

| Metric | Tier | Skippable when |
|---|---|---|
| `repo_health` | static | never (always-on) |
| `fullstack` | static | always evaluated |
| `ai_usage` | static → Gemini | always evaluated |
| `agent_analysis` | Gemini | always evaluated when LLM enabled |
| `solution_fit` | Gemini | needs `context.provided_context` |

## Env vars

| Var | Purpose |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | GCP / Firebase project (`GCP_PROJECT_ID` still accepted) |
| `GOOGLE_CLOUD_LOCATION` | Vertex region (e.g. `us-central1`) |
| `GEMINI_MODEL` | e.g. `gemini-2.5-flash` (`VERTEX_MODEL` still accepted) |
| `VERTEX_ENABLED` | `true`/`false` |
| `FIRESTORE_COLLECTION_JOBS` | default `githubanalysis_jobs` |
| `FIREBASE_PRIVATE_KEY` / `FIREBASE_CLIENT_EMAIL` | Local Firebase + GCP auth (no ADC) |
| `GITHUB_TOKEN` | GitHub API rate limits |
| `RUBRIC_WEIGHTS_JSON` | Default rubric weights (JSON array); see `GET /rubrics` |
| `CLOUD_RUN_JOB_NAME` | Worker job name. Unset = inline analysis on the API process |
| `CLOUD_RUN_JOBS_LOCATION` | Region for the worker job (defaults to `GOOGLE_CLOUD_LOCATION`) |
| `ANALYSIS_JOB_ID` | Set per worker execution; required for `python -m app.worker` |
