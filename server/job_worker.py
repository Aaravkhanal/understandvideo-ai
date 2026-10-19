"""Durable local worker for imports, timeline extraction, transcripts and retention.

Run separately from the API in production-like deployments:
    python -m server.job_worker
"""

from __future__ import annotations

import logging
import os
import time

from server.main import initialize, purge_expired_videos, run_pending_analysis_jobs, run_pending_import_jobs


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("understandvideo.worker")


def run_forever() -> None:
    initialize()
    poll_seconds = max(1, int(os.getenv("JOB_POLL_SECONDS", "3")))
    logger.info("Background worker started; polling every %s seconds", poll_seconds)
    while True:
        try:
            purge_expired_videos()
            run_pending_import_jobs()
            run_pending_analysis_jobs()
        except Exception:
            logger.exception("Worker cycle failed; the next cycle will retry")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    run_forever()
