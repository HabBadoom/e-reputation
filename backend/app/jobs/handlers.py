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


@handler("nlp.analyze")
def nlp_analyze(db: Session, job: Job) -> None:
    import uuid

    from app.nlp.service import analyze_mentions
    try:
        ids = [uuid.UUID(x) for x in job.payload["mention_ids"]]
    except (KeyError, ValueError, TypeError) as e:
        raise PermanentJobError("nlp.analyze needs mention_ids") from e
    if job.tenant_id is None:
        raise PermanentJobError("nlp.analyze needs a tenant")
    stats = analyze_mentions(db, job.tenant_id, ids)
    log.info("nlp_analyzed", extra=stats)


@handler("nlp.benchmark")
def nlp_benchmark(db: Session, job: Job) -> None:
    import uuid

    from app.db.models import LanguageEvaluationRun
    from app.db.session import set_context
    from app.nlp.service import run_benchmark
    try:
        run_id = uuid.UUID(job.payload["run_id"])
        admin = uuid.UUID(job.payload["requested_by"])
    except (KeyError, ValueError) as e:
        raise PermanentJobError("nlp.benchmark needs run_id and requested_by") from e
    set_context(db, tenant_id=None, user_id=admin)   # corpus is readable by platform admins only
    run = db.get(LanguageEvaluationRun, run_id)
    if run is None:
        raise PermanentJobError("evaluation run not found")
    run_benchmark(db, run)
