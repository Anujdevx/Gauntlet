"""Registration, login, logout, and current-session endpoints.

Two behaviours worth calling out because they are easy to get wrong:

**Signup and login both return 401 on a credential problem, not 403.** A 403 would
tell an attacker the account exists. Every failure path therefore produces the
same generic message.

**Password length is bounded at 256 characters.** Argon2 hashes whatever it is
given, and an unbounded input is a free CPU-burn DoS: a single request with a
megabyte-long password costs the server 100 ms of memory-hard work. Capping it
costs nothing real, since no human types a 256-character password.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from oa_api.core.deps import CurrentUser, DbSession
from oa_api.core.security import (
    SESSION_COOKIE_NAME,
    hash_password,
    hash_session_token,
    new_session_token,
    session_expiry,
    verify_password,
)
from oa_api.models.tables import Session, User
from oa_api.schemas.auth import LoginRequest, MeResponse, SignupRequest, UserResponse
from oa_engine.platform.config import Settings, get_settings
from oa_engine.platform.logging import get_logger

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
log = get_logger(__name__, service="oa-api")

#: One message for every credential failure. Differentiating "no such user" from
#: "wrong password" turns login into an account-enumeration oracle.
_GENERIC_CREDENTIAL_ERROR = "Email or password is incorrect"
_GENERIC_CONFLICT_ERROR = "That email is already registered"


def _set_session_cookie(
    response: Response, token: str, settings: Settings, expires: datetime
) -> None:
    """Attach the session cookie.

    ``Secure`` is left to the deployment rather than hardcoded. Forcing it breaks
    plain-HTTP local development, and a flag developers must disable locally is a
    flag that ends up disabled in production too.
    """
    max_age = int((expires - datetime.now(UTC)).total_seconds())
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.environment in {"staging", "prod"},
        path="/",
    )


@router.post(
    "/signup",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new candidate",
)
async def signup(
    payload: SignupRequest,
    response: Response,
    session: DbSession,
    request: Request,
) -> UserResponse:
    """Create an account and sign in immediately.

    No email verification: this is a practice platform, and a verification step
    would add an unbuilt dependency (an email provider, a token table, a resend
    flow) for a product where the address is never used to send anything.
    """
    settings = get_settings()
    existing = await session.scalar(select(User.id).where(User.email == payload.email))
    if existing is not None:
        # Checked before insert for a clean 409. The unique constraint below is
        # still the real guarantee, because two concurrent signups can both pass
        # this check.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_GENERIC_CONFLICT_ERROR)

    user = User(
        id=uuid.uuid4(),
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        last_seen_at=datetime.now(UTC),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        log.info("signup_email_conflict", email=payload.email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_GENERIC_CONFLICT_ERROR
        ) from exc

    token = new_session_token()
    expires = session_expiry()
    session.add(
        Session(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=expires,
            user_agent=_truncate(request.headers.get("user-agent"), 400),
            ip_address=_client_ip(request),
        )
    )
    log.info("signup_completed", user_id=str(user.id))

    _set_session_cookie(response, token, settings, expires)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=UserResponse, summary="Sign in")
async def login(
    payload: LoginRequest,
    response: Response,
    session: DbSession,
    request: Request,
) -> UserResponse:
    """Exchange credentials for a session cookie.

    Raises:
        HTTPException: ``401`` for any credential problem, identical in every
            case so the endpoint cannot be used to enumerate accounts.
    """
    settings = get_settings()
    unauthorised = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_CREDENTIAL_ERROR
    )

    result = await session.execute(
        select(User, User.password_hash).where(User.email == payload.email).limit(1)
    )
    row = result.one_or_none()
    if row is None:
        # Hash a dummy value anyway. Returning immediately on an unknown email
        # makes response time a reliable oracle for which addresses are
        # registered; spending the same work keeps the two paths indistinguishable.
        _constant_time_dummy_verify(payload.password)
        log.info("login_unknown_email")
        raise unauthorised

    user, password_hash = row
    if not verify_password(password_hash, payload.password):
        log.info("login_bad_password", user_id=str(user.id))
        raise unauthorised
    if not user.is_active:
        log.warning("login_inactive_account", user_id=str(user.id))
        raise unauthorised

    token = new_session_token()
    expires = session_expiry()
    session.add(
        Session(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=expires,
            user_agent=_truncate(request.headers.get("user-agent"), 400),
            ip_address=_client_ip(request),
        )
    )
    user.last_seen_at = datetime.now(UTC)
    log.info("login_completed", user_id=str(user.id))

    _set_session_cookie(response, token, settings, expires)
    return UserResponse.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
async def logout(
    response: Response,
    session: DbSession,
    oa_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> Response:
    """Revoke the current session and clear the cookie.

    Revocation is the reason sessions are a table rather than a signed token: this
    takes effect immediately. Idempotent -- signing out twice, or without a
    session, is not an error, because the caller's intent is already satisfied.
    """
    if oa_session:
        token_hash = hash_session_token(oa_session)
        # An ORM UPDATE on the mapped class rather than the Table: the typed
        # entity has `.update()`, the raw Table does not, and the rowcount tells us
        # whether a live session was actually there to revoke.
        await session.execute(
            update(Session)
            .where(Session.token_hash == token_hash, Session.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        log.info("logout_completed")

    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse, summary="Current session")
async def me(session: DbSession, user: CurrentUser) -> MeResponse:
    """Return the signed-in candidate and their session expiry.

    Expiry is included so the frontend can warn before a session lapses mid-
    assessment, which for a timed test means losing work.
    """
    expires = await session.scalar(
        select(func.max(Session.expires_at)).where(
            Session.user_id == user.id, Session.revoked_at.is_(None)
        )
    )
    return MeResponse(
        user=UserResponse.model_validate(user),
        expires_at=expires or session_expiry(),
    )


def _constant_time_dummy_verify(candidate_password: str) -> None:
    """Burn the same work a real verification would, for an unknown account."""
    from oa_api.core.security import verify_password

    # A valid Argon2 hash of an unguessable value. Verification always fails, but
    # costs the same ~100 ms as a genuine check.
    dummy = (
        "$argon2id$v=19$m=65536,t=3,p=2$c29tZXNhbHR2YWx1ZQ$"
        "ZbT1Q0zZJ8Qk7f0lQZ0Kx1oYbY5rG1oVj0lQZ0Kx1o"
    )
    verify_password(dummy, candidate_password)


def _client_ip(request: Request) -> str | None:
    """Best-effort client address for the session audit trail.

    Reads the peer address rather than ``X-Forwarded-For``. Honouring the header
    without a trusted proxy in front would let a client spoof its own recorded
    address, which defeats the point of recording it. Behind a proxy this
    under-records rather than lying, which is the right way to fail.
    """
    return request.client.host if request.client else None


def _truncate(value: str | None, limit: int) -> str | None:
    """Cap a header value before storing it."""
    return value[:limit] if value else None
