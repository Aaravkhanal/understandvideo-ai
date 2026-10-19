"""Durable local worker for imports, timeline extraction, transcripts and retention.

Run separately from the API in production-like deployments:
    python -m server.job_worker
"""

from __future__ import annotations

import logging
import os
from server.main import initialize, purge_expired_videos, run_pending_analysis_jobs, run_pending_import_jobs
from server.job_queue import wait_for_jobs, worker_sweep_lease


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("understandvideo.worker")


def run_forever() -> None:
    initialize()
    poll_seconds = max(1, int(os.getenv("JOB_POLL_SECONDS", "3")))
    logger.info("Background worker started; durable sweep interval is %s seconds", poll_seconds)
    while True:
        try:
            with worker_sweep_lease(max(60, poll_seconds * 4)) as acquired:
                if acquired:
                    purge_expired_videos()
                    run_pending_import_jobs()
                    run_pending_analysis_jobs()
        except Exception:
            logger.exception("Worker cycle failed; the next cycle will retry")
        wait_for_jobs(poll_seconds)


if __name__ == "__main__":
    run_forever()
