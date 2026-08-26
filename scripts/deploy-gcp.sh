#!/usr/bin/env bash
# One-shot Cloud Run deploy helper. Requires gcloud auth + PROJECT_ID.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-github-analyser}"
BUCKET="${GCS_BUCKET:-${PROJECT_ID}-github-analyser-cache}"
MODEL="${VERTEX_MODEL:-gemini-2.0-flash}"

gcloud builds submit --config cloudbuild.yaml \
  --project="$PROJECT_ID" \
  --substitutions="_REGION=${REGION},_SERVICE=${SERVICE},_GCS_BUCKET=${BUCKET},_VERTEX_MODEL=${MODEL}"
