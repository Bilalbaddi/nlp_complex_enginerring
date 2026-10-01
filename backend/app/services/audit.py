from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import AuditLog, User


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def record(
    db: Session,
    action: str,
    user: User | None = None,
    *,
    user_email: str | None = None,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    detail: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """Append an audit entry and commit it in its own transaction step."""
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            user_email=user.email if user else user_email,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            detail=detail,
            ip_address=client_ip(request),
        )
    )
    db.commit()
