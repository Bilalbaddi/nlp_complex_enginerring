from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import invalidate_user_cache, require_admin
from app.core.errors import ApiError
from app.core.security import hash_password
from app.db.models import AuditLog, Department, User, UserRole, Ward
from app.db.session import get_db, get_read_db
from app.schemas.common import Envelope, Page, PageParams, make_page, ok
from app.schemas.user import AuditLogOut, UserCreate, UserOut, UserUpdate
from app.services import audit

router = APIRouter(tags=["admin"])


def _check_refs(db: Session, department_id: int | None, ward_id: int | None) -> None:
    if department_id is not None and db.get(Department, department_id) is None:
        raise ApiError(422, "invalid_reference", f"Department {department_id} does not exist")
    if ward_id is not None and db.get(Ward, ward_id) is None:
        raise ApiError(422, "invalid_reference", f"Ward {ward_id} does not exist")


@router.get("/users", response_model=Envelope[Page[UserOut]])
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    role: UserRole | None = None,
    q: str | None = Query(None, max_length=100),
    _: User = Depends(require_admin),
    db: Session = Depends(get_read_db),
) -> dict[str, object]:
    params = PageParams(page=page, page_size=page_size)
    stmt = select(User)
    if role:
        stmt = stmt.where(User.role == role)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(User.email.ilike(like) | User.full_name.ilike(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.id).offset(params.offset).limit(params.page_size)).all()
    return ok(make_page([UserOut.model_validate(r) for r in rows], total, params))


@router.post("/users", response_model=Envelope[UserOut], status_code=201)
def create_user(
    body: UserCreate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, object]:
    _check_refs(db, body.department_id, body.ward_id)
    user = User(
        email=body.email.lower(),
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=body.role,
        department_id=body.department_id,
        ward_id=body.ward_id,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "email_exists", "A user with this email already exists") from exc
    audit.record(
        db, "user_create", admin, resource_type="user", resource_id=user.id,
        detail={"email": user.email, "role": user.role.value}, request=request,
    )
    return ok(UserOut.model_validate(user))


@router.patch("/users/{user_id}", response_model=Envelope[UserOut])
def update_user(
    user_id: int,
    body: UserUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "not_found", "User not found")
    changes = body.model_dump(exclude_unset=True)
    if user.id == admin.id and (changes.get("is_active") is False or (
        "role" in changes and changes["role"] != UserRole.ADMIN
    )):
        raise ApiError(400, "self_lockout", "You cannot deactivate or demote your own account")
    _check_refs(db, changes.get("department_id"), changes.get("ward_id"))
    password = changes.pop("password", None)
    for field, value in changes.items():
        setattr(user, field, value)
    if password:
        user.hashed_password = hash_password(password)
    db.commit()
    invalidate_user_cache(user.id)
    audit.record(
        db, "user_update", admin, resource_type="user", resource_id=user.id,
        detail={"fields": sorted([*changes, *(["password"] if password else [])])}, request=request,
    )
    return ok(UserOut.model_validate(user))


@router.get("/audit-logs", response_model=Envelope[Page[AuditLogOut]])
def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: str | None = Query(None, max_length=64),
    _: User = Depends(require_admin),
    db: Session = Depends(get_read_db),
) -> dict[str, object]:
    params = PageParams(page=page, page_size=page_size)
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AuditLog.id.desc()).offset(params.offset).limit(params.page_size)
    ).all()
    return ok(make_page([AuditLogOut.model_validate(r) for r in rows], total, params))
