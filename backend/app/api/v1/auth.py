from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.core.ratelimit import login_rate_limit
from app.core.security import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import Envelope, ok
from app.schemas.user import LoginRequest, RefreshRequest, TokenPair, UserOut
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against when the email is unknown, so response time doesn't reveal which emails exist.
_DUMMY_HASH = hash_password("dummy-password-1")


def _issue(user: User) -> TokenPair:
    access, expires_in = create_token(user.id, user.role.value, "access")
    refresh, _ = create_token(user.id, user.role.value, "refresh")
    return TokenPair(
        access_token=access, refresh_token=refresh, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.post("/login", response_model=Envelope[TokenPair], dependencies=[Depends(login_rate_limit())])
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    valid = verify_password(body.password, user.hashed_password if user else _DUMMY_HASH)
    if not user or not valid or not user.is_active:
        audit.record(db, "login_failed", user_email=body.email.lower(), request=request)
        raise ApiError(401, "invalid_credentials", "Incorrect email or password")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    audit.record(db, "login", user, resource_type="user", resource_id=user.id, request=request)
    return ok(_issue(user))


@router.post("/refresh", response_model=Envelope[TokenPair], dependencies=[Depends(login_rate_limit())])
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except TokenError as exc:
        raise ApiError(401, "invalid_token", "Invalid or expired refresh token") from exc
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise ApiError(401, "invalid_token", "User not found or inactive")
    return ok(_issue(user))


@router.get("/me", response_model=Envelope[UserOut])
def me(user: User = Depends(get_current_user)) -> dict[str, object]:
    return ok(UserOut.model_validate(user))


@router.post("/logout", response_model=Envelope[None])
def logout(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    """Tokens are stateless; the client discards them. We record the event for the audit trail."""
    audit.record(db, "logout", user, resource_type="user", resource_id=user.id, request=request)
    return ok(None)
