"""Job handlers. Each runs in its own transaction with the DB context set to the job's
tenant (no user). Raise to fail (and retry); raise PermanentJobError to dead-letter now."""

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.jobs.queue import Job
from app.modules.notifications.email import EmailDeliveryError, EmailMessage, get_email_provider

log = logging.getLogger("repintel.jobs")


class PermanentJobError(Exception):
    """Retrying cannot help (bad payload, deleted target…)."""


Handler = Callable[[Session, Job], None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return deco


@handler("email.send")
def send_email(db: Session, job: Job) -> None:
    p = job.payload
    try:
        msg = EmailMessage(to_email=p["to_email"], to_name=p.get("to_name", ""),
                           subject=p["subject"], html=p["html"], text=p["text"],
                           tags=tuple(p.get("tags", [])))
    except KeyError as e:
        raise PermanentJobError(f"malformed email payload: missing {e}") from e
    try:
        get_email_provider().send(msg)
    except EmailDeliveryError:
        raise  # transient: retried with backoff


@handler("monitoring.run")
def monitoring_run(db: Session, job: Job) -> None:
    from app.modules.monitoring.runner import execute_run  # avoid import cycle
    execute_run(db, job)
