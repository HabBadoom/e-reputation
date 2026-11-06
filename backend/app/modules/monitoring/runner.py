"""Executes a monitoring run (job kind "monitoring.run") through the collection pipeline."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.collection.pipeline import collect_for_project
from app.db.models import Brand, MonitoringProject, MonitoringRun
from app.jobs.handlers import PermanentJobError
from app.jobs.queue import Job

log = logging.getLogger("repintel.monitoring")


def execute_run(db: Session, job: Job) -> None:
    try:
        project_id = uuid.UUID(job.payload["project_id"])
    except (KeyError, ValueError) as e:
        raise PermanentJobError("monitoring.run payload needs a valid project_id") from e
    project = db.get(MonitoringProject, project_id)
    if project is None or project.tenant_id != job.tenant_id:
        log.info("monitoring_run_project_gone", extra={"project_id": str(project_id)})
        return  # deleted meanwhile: nothing to do, job succeeds

    run = None
    if run_id := job.payload.get("run_id"):
        run = db.get(MonitoringRun, uuid.UUID(run_id))
    if run is None:
        run = MonitoringRun(tenant_id=project.tenant_id, project_id=project.id,
                            trigger=job.payload.get("trigger", "schedule"))
        db.add(run)
    run.status, run.started_at = "running", datetime.now(UTC)
    db.flush()

    brand = db.get(Brand, project.brand_id)
    if brand is None or brand.status != "active":
        run.status, run.summary = "skipped", {"reason": "brand_not_active"}
    else:
        try:
            with db.begin_nested():   # a crash rolls back partial writes, keeps the run row
                run.status, run.summary = collect_for_project(db, project, run)
        except Exception as e:  # noqa: BLE001 — record the failure on the run, don't retry
            log.exception("monitoring_run_failed", extra={"run_id": str(run.id)})
            run.status = "failed"
            run.error = f"{type(e).__name__}: internal error during collection"
            run.summary = {"reason": "internal_error"}
    run.finished_at = datetime.now(UTC)
    project.last_run_at = run.finished_at
