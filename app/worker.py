"""Cloud Run Job entrypoint: analyze one Firestore job and exit.

Usage:
    ANALYSIS_JOB_ID=<job_id> python -m app.worker
    python -m app.worker <job_id>
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from app.pipeline.runner import run_pipeline

logger = logging.getLogger(__name__)


def resolve_job_id(argv: list[str] | None = None) -> str:
    args = list(sys.argv[1:] if argv is None else argv)
    env_id = (os.environ.get("ANALYSIS_JOB_ID") or "").strip()
    if env_id:
        return env_id
    if args and not args[0].startswith("-"):
        return args[0].strip()
    return ""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    job_id = resolve_job_id()
    if not job_id:
        logger.error("ANALYSIS_JOB_ID is required")
        raise SystemExit(2)
    logger.info(
        "Starting analysis worker job_id=%s execution=%s attempt=%s",
        job_id,
        os.environ.get("CLOUD_RUN_EXECUTION") or "local",
        os.environ.get("CLOUD_RUN_TASK_ATTEMPT") or "0",
    )
    asyncio.run(run_pipeline(job_id))


if __name__ == "__main__":
    main()
