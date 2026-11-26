"""Append-only Scanner audit trail. The app DB role has INSERT/SELECT only on it."""

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.db.models import ScannerAuditLog
from app.modules.audit.service import _valid_ip


def record(
    db: Session,
    *,
    account_id: uuid.UUID,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    db.add(
        ScannerAuditLog(
            account_id=account_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            meta=meta or {},
            ip=_valid_ip(request.client.host if request and request.client else None),
            request_id=request_id_var.get(),
        )
    )
