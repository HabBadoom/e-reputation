import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.db.models import AuditLog, User
from app.db.session import get_db

router = APIRouter(prefix="/audit-logs", tags=["audit"])


class AuditOut(BaseModel):
    id: uuid.UUID
    action: str
    actor_email: str | None
    target_type: str | None
    target_id: uuid.UUID | None
    metadata: dict[str, Any]
    ip: str | None
    request_id: str | None
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditOut]
    total: int
    limit: int
    offset: int


@router.get("", response_model=AuditPage)
def list_audit(
    ctx: AuthContext = Depends(require("audit:read")),
    db: Session = Depends(get_db),
    action: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditPage:
    stmt = select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_user_id).where(
        AuditLog.tenant_id == ctx.tenant_id
    )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)).all()
    return AuditPage(
        items=[AuditOut(id=a.id, action=a.action, actor_email=email, target_type=a.target_type,
                        target_id=a.target_id, metadata=a.meta, ip=str(a.ip) if a.ip else None,
                        request_id=a.request_id, created_at=a.created_at) for a, email in rows],
        total=total, limit=limit, offset=offset,
    )
