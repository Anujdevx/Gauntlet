"""Shared FastAPI dependencies: database session, current user, settings.

The auth dependency is the security boundary for every domain route, so it is
written to fail closed in one place rather than re-implemented per router:

* An unauthenticated request gets ``401`` before any handler runs, so no route
  can forget the check.
* Ownership is verified by *querying the submission with the user id in the
  ``WHERE`` clause*, never by fetching then comparing. A fetch-then-compare would
  leak existence through a 403-versus-404 difference, which is an enumeration
  oracle for submission ids.
* An inactive account is rejected even with a valid, unexpired token. Disabling a
  user has to actually disable them.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oa_api.core.security import SESSION_COOKIE_NAME, hash_session_token
from oa_api.models.db import get_sessionmaker
from oa_api.models.tables import Session, User
from oa_engine.platform.config import Settings, get_settings
from oa_engine.platform.logging import get_logger

log = get_logger(__name__, service="oa-api")

__all__ = [
    "CurrentUser",
    "DbSession",
    "SettingsDep",
    "get_current_user",
    "get_db",
]


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session.

    Rolls back on any exception and commits on clean exit. The rollback matters
    more than it looks: without it, a handler that raises half way through leaves
    an open transaction holding locks, and the next request contends on them.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    session: DbSession,
    request: Request,
    oa_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> User:
    """Resolve the signed-in candidate from the session cookie.

    Args:
        session: Request-scoped database session.
        request: Used for the client address, recorded on session creation.
        oa_session: The raw cookie value, if present.

    Returns:
        The authenticated :class:`User`.

    Raises:
        HTTPException: ``401`` when the cookie is absent, unknown, revoked,
            expired, or belongs to a deactivated account.
    """
    unauthorised = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Cookie"},
    )
    if not oa_session:
        raise unauthorised

    token_hash = hash_session_token(oa_session)
    now_result = await session.execute(
        select(Session, User)
        .join(User, User.id == Session.user_id)
        .where(
            Session.token_hash == token_hash,
            Session.revoked_at.is_(None),
        )
        .limit(1)
    )
    row = now_result.one_or_none()
    if row is None:
        log.info("auth_session_not_found")
        raise unauthorised

    db_session, user = row
    if db_session.expires_at <= datetime.now(UTC):
        log.info("auth_session_expired", user_id=str(user.id))
        raise unauthorised

    if not user.is_active:
        # A valid token for a disabled account must not grant access, or
        # deactivating an account would be advisory rather than effective.
        log.warning("auth_user_inactive", user_id=str(user.id))
        raise unauthorised

    log.debug("auth_ok", user_id=str(user.id), path=request.url.path)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_uuid(value: str, *, field: str = "id") -> uuid.UUID:
    """Parse a path parameter as a UUID, or fail with a clear message.

    A malformed identifier is a client error, and saying "not a valid UUID" is more
    useful than a database error about a malformed key.

    Raises:
        HTTPException: ``422`` if the value is not a UUID.
    """
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be a UUID",
        ) from exc
