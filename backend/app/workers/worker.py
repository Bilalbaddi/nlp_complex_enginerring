"""Polling worker: `python -m app.workers.worker` (or `python scripts/tasks.py worker`).

Not needed during development when JOBS_INLINE is on; run it for background processing without inline mode.
"""

import argparse
import logging
import time

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services import jobs

log = logging.getLogger("worker")
CLEANUP_EVERY_S = 600


def run_worker(poll_interval: float = 2.0, once: bool = False) -> None:
    jobs.registered_types()  # import handlers
    log.info("worker started (poll every %.1fs); job types: %s", poll_interval, ", ".join(jobs.registered_types()))
    last_cleanup = 0.0
    try:
        while True:
            if jobs.run_job():
                continue  # drain the queue before sleeping
            if once:
                return
            if time.monotonic() - last_cleanup > CLEANUP_EVERY_S:
                jobs.enqueue("system.cleanup")
                last_cleanup = time.monotonic()
                continue
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        log.info("worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="SG-DSS job worker")
    parser.add_argument("--poll", type=float, default=2.0, help="seconds between queue polls when idle")
    parser.add_argument("--once", action="store_true", help="process due jobs then exit")
    args = parser.parse_args()
    setup_logging(get_settings().log_level)
    run_worker(args.poll, args.once)


if __name__ == "__main__":
    main()
