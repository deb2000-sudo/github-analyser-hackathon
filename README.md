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

# GCS cache bucket
gsutil mb -l "$REGION" "gs://${BUCKET}" 2>/dev/null || true

# Create Secret Manager secrets once (values from your .env) — see cloudbuild.yaml header
# Secret ids: firebase-project-id, firebase-private-key-id, firebase-private-key,
#   firebase-client-email, firebase-client-id, firebase-database-url,
#   firebase-web-api-key, github-token

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
RUN_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Cloud Run runtime SA permissions
for ROLE in roles/aiplatform.user roles/datastore.user roles/storage.objectAdmin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${RUN_SA}" --role="$ROLE" --quiet
done

# Cloud Build SA — deploy + secret IAM (cloudbuild.yaml ensure-secret-access step)
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser roles/secretmanager.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${CB_SA}" --role="$ROLE" --quiet
done
```

Firebase and `GITHUB_TOKEN` are mounted from Secret Manager by `cloudbuild.yaml` (not plain env vars). See substitutions `_SEC_FIREBASE_*` and `_GITHUB_TOKEN_SECRET` in that file.

### 2. Deploy with Cloud Build

```bash
gcloud builds submit --config cloudbuild.yaml \
  --substitutions=_REGION=${REGION},_GCS_BUCKET=${BUCKET} \
  --project="$PROJECT_ID"
```

The final deploy step prints the **Cloud Run URL**.

**Substitutions** (optional overrides):

| Var | Default | Purpose |
|---|---|---|
| `_REGION` | `us-central1` | Cloud Run + Vertex region |
| `_GCS_BUCKET` | *(required)* | `EVALUATION_BUCKET_NAME` |
| `_SERVICE` | `github-analyser` | Cloud Run service name |
| `_VERTEX_MODEL` | `gemini-2.5-flash` | Gemini model |
| `_GITHUB_TOKEN_SECRET` | `github-token` | Secret id for `GITHUB_TOKEN` |
| `_SEC_FIREBASE_*` | see table above | Firebase secret ids |
| `_MOUNT_SECRETS` | `true` | Set `false` to skip all secrets (ADC only) |
| `_RUN_SERVICE_ACCOUNT` | *(empty)* | Custom runtime SA email |

**Notes:**
- All Firebase + `GITHUB_TOKEN` env vars come from Secret Manager via `cloudbuild.yaml`.
- Vertex AI uses the **Cloud Run runtime SA** (ADC), not the Firebase Admin key.
- Create secrets once before first deploy (instructions in `cloudbuild.yaml` header).
- Image tag uses `$BUILD_ID` (works with `gcloud builds submit` and Git triggers).

### Manual deploy (alternative)

```bash
gcloud run deploy github-analyser \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},EVALUATION_BUCKET_NAME=${BUCKET},GEMINI_MODEL=gemini-2.5-flash,FIRESTORE_COLLECTION_JOBS=githubanalysis_jobs" \
  --set-secrets "GITHUB_TOKEN=github-token:latest" \
  --memory 1Gi \
  --timeout 300 \
  --project "$PROJECT_ID"
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
