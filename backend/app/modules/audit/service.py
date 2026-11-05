"""Append-only audit trail. The app DB role has INSERT/SELECT only on audit_logs."""

import ipaddress
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.db.models import AuditLog


def _valid_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        ipaddress.ip_address(ip)
        return ip
    except ValueError:
        return None  # e.g. "testclient"


def record(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            meta=meta or {},
            ip=_valid_ip(request.client.host if request and request.client else None),
            request_id=request_id_var.get(),
        )
    )
