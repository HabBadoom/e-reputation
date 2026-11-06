"""Executes a monitoring run (job kind "monitoring.run").

Until source connectors exist (Milestone 3) a run builds and records the query plan, then
ends as `skipped` with an explicit reason. It never invents mentions or results.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import Brand, MonitoringProject, MonitoringRun
from app.jobs.handlers import PermanentJobError
from app.jobs.queue import Job
from app.modules.monitoring.query_plan import load_query_plan

log = logging.getLogger("repintel.monitoring")

NO_CONNECTORS = ("No source connector is enabled yet (Milestone 3). The query plan was "
                 "built; no content was collected.")


def execute_run(db: Session, job: Job) -> None:
    try:
        project_id = uuid.UUID(job.payload["project_id"])
    except (KeyError, ValueError) as e:
        raise PermanentJobError("monitoring.run payload needs a valid project_id") from e
    project = db.get(MonitoringProject, project_id)
    if project is None or project.tenant_id != job.tenant_id:
        log.info("monitoring_run_project_gone", extra={"project_id": str(project_id)})
        return  # deleted meanwhile: nothing to do, job succeeds

    now = datetime.now(UTC)
    run = None
    if run_id := job.payload.get("run_id"):
        run = db.get(MonitoringRun, uuid.UUID(run_id))
    if run is None:
        run = MonitoringRun(tenant_id=project.tenant_id, project_id=project.id,
                            trigger=job.payload.get("trigger", "schedule"))
        db.add(run)
    run.status, run.started_at = "running", now

    brand = db.get(Brand, project.brand_id)
    if brand is None or brand.status != "active":
        run.status, run.summary = "skipped", {"reason": "brand_not_active"}
    else:
        plan = load_query_plan(db, project)
        run.status = "skipped"
        run.summary = {
            "reason": "no_active_connectors",
            "detail": NO_CONNECTORS,
            "query_groups": len(plan),
            "search_terms": sum(len(g.terms) for g in plan),
            "mentions_collected": 0,
        }
    run.finished_at = datetime.now(UTC)
    project.last_run_at = run.finished_at
