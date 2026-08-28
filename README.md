# Repo Analysis Microservice (GCP)

Pluggable GitHub repo analysis for hackathon submissions.

| Layer | Service |
|---|---|
| API | FastAPI on **Cloud Run** |
| Jobs | **Firestore** (Firebase Admin) |
| Cache | **GCS** (repo snapshots by `owner/repo@sha`) |
| LLM | **Vertex AI Gemini** |

The caller selects which metrics to run per request — the service never hardcodes “always check everything.”

## Quick start (local)

Requires [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
cp .env.example .env
# fill Firebase SA fields, GOOGLE_CLOUD_PROJECT, EVALUATION_BUCKET_NAME, GITHUB_TOKEN
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
export BUCKET=${PROJECT_ID}-github-analyser-cache

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  --project="$PROJECT_ID"

# Native Firestore (Firebase) — create once in console or:
gcloud firestore databases create --location="$REGION" --project="$PROJECT_ID"

gsutil mb -l "$REGION" "gs://${BUCKET}"
```

### IAM (Cloud Run runtime SA)

Grant the Cloud Run service account:

- `roles/aiplatform.user` — Vertex AI Gemini
- `roles/datastore.user` — Firestore
- `roles/storage.objectAdmin` — GCS cache bucket (or objectCreator + objectViewer)

## Deploy (Cloud Build → Cloud Run)

### 1. One-time GCP setup

```bash
export PROJECT_ID=your-gcp-project
export REGION=us-central1
export BUCKET=${PROJECT_ID}-hackathon-evaluations

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project="$PROJECT_ID"

# Firestore (once per project)
gcloud firestore databases create --location="$REGION" --project="$PROJECT_ID" 2>/dev/null || true

# GCS cache bucket (optional — cloudbuild.yaml Step 4 creates it if missing)
gsutil mb -l "$REGION" "gs://${BUCKET}" 2>/dev/null || true

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

# Cloud Build SA — deploy to Cloud Run + push images
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${CB_SA}" --role="$ROLE" --quiet
done
```

Runtime SA IAM (Vertex, Firestore, GCS, secrets) is applied automatically in **cloudbuild.yaml Step 5** on each deploy.

**Secrets** (already in Secret Manager): `FIREBASE_PROJECT_ID`, `FIREBASE_PRIVATE_KEY`, `FIREBASE_CLIENT_EMAIL`, `FIREBASE_DATABASE_URL`, `FIREBASE_WEB_API_KEY`, `GITHUB_TOKEN`.

**Plain env vars** (Cloud Build substitutions in `cloudbuild.yaml`): Vertex AI + GCS settings.

### 2. Deploy with Cloud Build

```bash
gcloud builds submit --config cloudbuild.yaml --project=nxt-acad-hackathon
```

The final deploy step prints the **Cloud Run URL**. Vertex AI + GCS settings are baked into `cloudbuild.yaml` substitutions; override only if needed:

```bash
gcloud builds submit --config cloudbuild.yaml \
  --substitutions=_GCS_BUCKET=my-other-bucket,_GEMINI_MODEL=gemini-2.0-flash \
  --project=nxt-acad-hackathon
```

**Substitutions** (optional overrides):

| Var | Default | Purpose |
|---|---|---|
| `_REGION` | `us-central1` | Cloud Run, Vertex, GCS region |
| `_ENVIRONMENT` | `production` | `ENVIRONMENT` env var |
| `_GCP_LOCATION` | `us-central1` | `GOOGLE_CLOUD_LOCATION` |
| `_GCS_BUCKET` | `nxt-acad-hackathon-hackathon-evaluations` | GCS cache bucket |
| `_GEMINI_MODEL` | `gemini-2.5-flash` | Vertex Gemini model |
| `_GCS_CACHE_PREFIX` | `github-cache` | GCS object prefix |
| `_FIRESTORE_COLLECTION` | `github_analysis_jobs` | Firestore jobs collection |
| `_RUN_SERVICE_ACCOUNT` | `nxt-acad-ai-hackathon-evaluate@nxt-acad-hackathon.iam.gserviceaccount.com` | Cloud Run runtime SA |

**Notes:**
- Cloud Run uses `_RUN_SERVICE_ACCOUNT` (not the default compute SA) so Secret Manager, Vertex, Firestore, and GCS permissions apply.
- Firebase + `GITHUB_TOKEN` mounted via `--update-secrets` (your existing Secret Manager names).
- Vertex AI uses the **Cloud Run runtime SA** (ADC), not the Firebase Admin key.
- Image tagged with `$BUILD_ID` (deploy), `$SHORT_SHA` (git triggers), and `latest`.

### Manual deploy (alternative)

```bash
gcloud run deploy github-analyser \
  --source . \
  --region us-central1 \
  --service-account nxt-acad-ai-hackathon-evaluate@nxt-acad-hackathon.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=nxt-acad-hackathon,GOOGLE_CLOUD_LOCATION=us-central1,EVALUATION_BUCKET_NAME=nxt-acad-hackathon-hackathon-evaluations,GEMINI_MODEL=gemini-2.5-flash,FIRESTORE_COLLECTION_JOBS=github_analysis_jobs" \
  --set-secrets "GITHUB_TOKEN=GITHUB_TOKEN:latest,FIREBASE_PROJECT_ID=FIREBASE_PROJECT_ID:latest,FIREBASE_PRIVATE_KEY=FIREBASE_PRIVATE_KEY:latest,FIREBASE_CLIENT_EMAIL=FIREBASE_CLIENT_EMAIL:latest,FIREBASE_DATABASE_URL=FIREBASE_DATABASE_URL:latest,FIREBASE_WEB_API_KEY=FIREBASE_WEB_API_KEY:latest" \
  --memory 1Gi \
  --timeout 300 \
  --project nxt-acad-hackathon
```

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /analyze` | Submit one repo |
| `POST /analyze/batch` | Submit many |
| `GET /analyze/{job_id}` | Poll status + result (Firestore) |
| `GET /analyze/{job_id}?wait_seconds=60` | Poll with long-wait (blocks up to 120s) |
| `POST /analyze/sync` | Submit + wait for full result in one call |
| `GET /metrics` | Metric catalogue + schemas |
| `GET /rubrics` | Default rubric weights + max total score |
| `GET /health` | Liveness |

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
| `EVALUATION_BUCKET_NAME` | Snapshot cache bucket (`GCS_BUCKET` still accepted) |
| `GEMINI_MODEL` | e.g. `gemini-2.5-flash` (`VERTEX_MODEL` still accepted) |
| `VERTEX_ENABLED` | `true`/`false` |
| `FIRESTORE_COLLECTION_JOBS` | default `githubanalysis_jobs` |
| `FIREBASE_PRIVATE_KEY` / `FIREBASE_CLIENT_EMAIL` | Local Firebase + GCP auth (no ADC) |
| `GITHUB_TOKEN` | GitHub API rate limits |
| `RUBRIC_WEIGHTS_JSON` | Default rubric weights (JSON array); see `GET /rubrics` |
#   g i t h u b - a n a l y s e r - h a c k a t h o n 
 
 # github-analyser-hackathon
