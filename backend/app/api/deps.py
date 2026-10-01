import time
from collections.abc import Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import TokenError, decode_token
from app.db.models import User, UserRole
from app.db.session import get_read_db

bearer = HTTPBearer(auto_error=False, description="JWT access token")

_user_cache: dict[int, tuple[float, User]] = {}


def invalidate_user_cache(user_id: int | None = None) -> None:
    """Call after changing a user (role, active flag) so the change applies immediately in this process."""
    if user_id is None:
        _user_cache.clear()
    else:
        _user_cache.pop(user_id, None)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_read_db),
) -> User:
    if creds is None:
        raise ApiError(401, "not_authenticated", "Missing bearer token")
    try:
        payload = decode_token(creds.credentials, "access")
    except TokenError as exc:
        raise ApiError(401, "invalid_token", "Invalid or expired token") from exc
    uid = int(payload["sub"])
    ttl = get_settings().user_cache_seconds
    cached = _user_cache.get(uid)
    if cached and ttl and time.monotonic() - cached[0] < ttl:
        user = cached[1]
    else:
        user = db.get(User, uid)
        if user is not None and ttl:
            _user_cache[uid] = (time.monotonic(), user)
    if user is None or not user.is_active:
        raise ApiError(401, "invalid_token", "User not found or inactive")
    request.state.user_id = user.id
    return user


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """RBAC dependency. Admin is always allowed; otherwise the user's role must be listed."""
    allowed = set(roles) | {UserRole.ADMIN}

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise ApiError(403, "forbidden", "Your role does not permit this action")
        return user

    return dependency


require_admin = require_roles()
require_any_role = require_roles(*UserRole)
