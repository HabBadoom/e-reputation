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
    # New labels can change trends, spikes and crises: evaluate soon (once while pending).
    from sqlalchemy import text
    db.execute(text("SELECT request_alert_evaluation()"))


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


@handler("alerts.evaluate")
def alerts_evaluate(db: Session, job: Job) -> None:
    from app.alerts.engine import evaluate_tenant
    if job.tenant_id is None:
        raise PermanentJobError("alerts.evaluate needs a tenant")
    evaluate_tenant(db, job.tenant_id)


@handler("webhook.deliver")
def webhook_deliver(db: Session, job: Job) -> None:
    import uuid

    from app.alerts.webhooks import WebhookDeliveryError, alert_payload, post
    from app.db.models import Alert, Webhook
    from app.db.session import session_scope, set_context
    try:
        hook_id = uuid.UUID(job.payload["webhook_id"])
        alert_id = uuid.UUID(job.payload["alert_id"])
    except (KeyError, ValueError, TypeError) as e:
        raise PermanentJobError("webhook.deliver needs webhook_id and alert_id") from e
    hook, alert = db.get(Webhook, hook_id), db.get(Alert, alert_id)
    if hook is None or alert is None:
        raise PermanentJobError("webhook or alert no longer exists")
    if not hook.enabled:
        return   # disabled since: drop silently (the alert stays visible in-app)
    try:
        post(hook, alert_payload(alert), alert.id)
    except WebhookDeliveryError:
        # This transaction is rolled back on failure: record the outcome separately so the
        # UI can show why deliveries fail, then raise to retry with backoff.
        status, error, at = hook.last_status, hook.last_error, hook.last_delivery_at
        with session_scope() as s2:
            set_context(s2, tenant_id=job.tenant_id, user_id=None)
            h2 = s2.get(Webhook, hook_id)
            if h2 is not None:
                h2.last_status, h2.last_error, h2.last_delivery_at = status, error, at
        raise


@handler("report.generate")
def report_generate(db: Session, job: Job) -> None:
    import uuid

    from app.db.models import Report
    from app.db.session import session_scope, set_context
    from app.reports.formats import generate
    try:
        report_id = uuid.UUID(job.payload["report_id"])
    except (KeyError, ValueError, TypeError) as e:
        raise PermanentJobError("report.generate needs report_id") from e
    report = db.get(Report, report_id)
    if report is None:
        raise PermanentJobError("report not found")
    try:
        with db.begin_nested():
            generate(db, report)
    except Exception as e:
        log.exception("report_failed", extra={"report_id": str(report_id)})
        with session_scope() as s2:
            set_context(s2, tenant_id=job.tenant_id, user_id=None)
            r2 = s2.get(Report, report_id)
            if r2 is not None:
                r2.status, r2.error = "failed", f"{type(e).__name__}: generation failed"
        raise PermanentJobError("report generation failed") from e
