"""Background worker: runs the monitoring scheduler and processes queued jobs.

    python -m app.jobs.worker            # long-running process (docker compose: `worker`)

`run_once()` is also used by tests to drain the queue synchronously.
"""

import logging
import os
import signal
import socket
import time

from app.core.config import get_settings
from app.core.logging import configure_logging, tenant_id_var
from app.db.session import session_scope, set_context
from app.jobs import queue
from app.jobs.handlers import HANDLERS, PermanentJobError

log = logging.getLogger("repintel.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
STALE_AFTER_SECONDS = 15 * 60


def _process(job: queue.Job) -> None:
    tenant_id_var.set(str(job.tenant_id) if job.tenant_id else None)
    fn = HANDLERS.get(job.kind)
    try:
        if fn is None:
            raise PermanentJobError(f"no handler for job kind '{job.kind}'")
        with session_scope() as db:
            set_context(db, tenant_id=job.tenant_id, user_id=None)
            fn(db, job)
            queue.complete(db, job.id)  # same transaction as the handler's DB writes
        log.info("job_succeeded", extra={"job_id": str(job.id), "kind": job.kind,
                                         "attempt": job.attempts})
    except Exception as e:  # noqa: BLE001 — every failure is recorded on the job
        permanent = isinstance(e, PermanentJobError)
        with session_scope() as db:
            status = queue.fail(db, job, f"{type(e).__name__}: {e}", retry=not permanent)
        log.log(logging.ERROR if status == "dead" else logging.WARNING, "job_failed",
                exc_info=not permanent,
                extra={"job_id": str(job.id), "kind": job.kind, "attempt": job.attempts,
                       "status": status})
    finally:
        tenant_id_var.set(None)


def run_once(batch: int = 20, *, schedule: bool = True) -> int:
    """One scheduler tick + one batch of jobs. Returns the number of jobs processed."""
    with session_scope() as db:
        queue.requeue_stale(db, STALE_AFTER_SECONDS)
        if schedule:
            n = queue.schedule_due_monitoring(db)
            if n:
                log.info("monitoring_scheduled", extra={"count": n})
            n = queue.schedule_alert_evaluations(db, get_settings().alert_eval_every_minutes)
            if n:
                log.info("alert_evaluations_scheduled", extra={"count": n})
    with session_scope() as db:
        jobs = queue.claim(db, WORKER_ID, batch)
    for job in jobs:
        _process(job)
    return len(jobs)


def main() -> None:
    configure_logging(get_settings().log_level)
    stopping = False

    def _stop(*_: object) -> None:
        nonlocal stopping
        stopping = True
        log.info("worker_stopping")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker_started", extra={"worker_id": WORKER_ID})
    while not stopping:
        try:
            processed = run_once()
        except Exception:  # noqa: BLE001 — keep the worker alive; DB may be restarting
            log.exception("worker_tick_failed")
            processed = 0
        if processed == 0:
            time.sleep(2)


if __name__ == "__main__":
    main()
