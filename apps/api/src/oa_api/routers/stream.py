"""WebSocket streaming of execution events.

The browser watches a submission execute and wants stdout as it happens. A WebSocket
gets it there; polling would mean a request every second or two per open tab, and
the output arrives in bursts anyway.

Design decisions worth stating:

**The stream is a convenience, never the source of truth.** Every event is
buffered in a Redis stream with a TTL, and the terminal result is in Postgres. If
the socket dies, the client polls. If the client was never connected, it polls.
That is why nothing here is load-bearing for correctness -- a stream that drops
events costs the candidate some animation, not their grade.

**Replay on reconnect.** A client that dropped sends the last stream id it saw and
receives what it missed, rather than a stream starting mid-output. Without that, a
brief network blip silently swallows the compile error the candidate needed.

**Authentication happens before the upgrade.** The cookie is checked in the
dependency, so an unauthenticated socket never reaches a stream. The candidate's
submission id is then checked for ownership against Postgres, because a valid
session must still not let one candidate watch another's execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from oa_api.core.security import SESSION_COOKIE_NAME, hash_session_token
from oa_api.models.db import get_sessionmaker
from oa_api.models.tables import Session, Submission, User
from oa_api.services import submission_store
from oa_engine.platform.config import get_settings
from oa_engine.platform.logging import get_logger

router = APIRouter(tags=["stream"])
log = get_logger(__name__, service="oa-api")

#: How long the loop waits for a new event before checking whether the submission
#: has finished. Short enough that a completed run's final result is not delayed
#: behind a long poll, long enough not to spin.
_POLL_INTERVAL_S: float = 0.25

#: Hard cap on how long one socket stays open. A candidate who leaves a tab open
#: on a finished submission would otherwise hold a task and a Redis reader for the
#: lifetime of the process.
_MAX_STREAM_SECONDS: float = 900.0


@router.websocket("/api/v1/submissions/{submission_id}/stream")
async def stream_submission(websocket: WebSocket, submission_id: str) -> None:
    """Stream execution events for one submission the caller owns.

    Closes with code ``4401`` when unauthenticated and ``4404`` when the
    submission is absent or not the caller's. The custom codes matter: a browser
    cannot read the HTTP status of a failed upgrade, so ``1008`` with a reason
    string is the only way to tell "your session expired, log in again" apart
    from "that submission does not exist".
    """
    settings = get_settings()

    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        await websocket.close(code=4401, reason="Not authenticated")
        return

    owner = await _resolve_owner(submission_id=submission_id, token=token)
    if owner is None:
        await websocket.close(code=4404, reason="Submission not found")
        return

    await websocket.accept()
    log.info("stream_opened", submission_id=submission_id)

    last_id = websocket.query_params.get("last_id", "0-0")
    deadline = asyncio.get_running_loop().time() + _MAX_STREAM_SECONDS
    emitted = 0

    try:
        # Replay first, so a reconnecting client catches up before live frames.
        for buffered in await submission_store.read_events_since(
            settings, submission_id=submission_id, last_id=last_id
        ):
            await _send(websocket, buffered)
            last_id = buffered["id"]
            emitted += 1

        while asyncio.get_running_loop().time() < deadline:
            if await _is_finished(submission_id=submission_id, user_id=owner):
                # Drain whatever landed between the last read and the finish, then
                # stop. The client re-fetches the durable result over HTTP, so a
                # missing final frame is not a lost result.
                for buffered in await submission_store.read_events_since(
                    settings, submission_id=submission_id, last_id=last_id
                ):
                    await _send(websocket, buffered)
                    emitted += 1
                await _send(
                    websocket,
                    {"event": "status", "data": "finished", "id": last_id},
                    seq=emitted,
                )
                return

            events = await submission_store.read_events_since(
                settings, submission_id=submission_id, last_id=last_id
            )
            if not events:
                # Nothing new. Sleep rather than spin, and let the client's own
                # idle timeout decide when to fall back to polling.
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue

            for buffered in events:
                await _send(websocket, buffered)
                last_id = buffered["id"]
                emitted += 1

    except WebSocketDisconnect:
        log.info("stream_client_disconnected", submission_id=submission_id)
    except Exception:
        log.exception("stream_failed", submission_id=submission_id)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="Stream error")
    finally:
        log.info("stream_closed", submission_id=submission_id, frames=emitted)
        with contextlib.suppress(Exception):
            await websocket.close()


async def _resolve_owner(*, submission_id: str, token: str) -> uuid.UUID | None:
    """Return the owning user id, or ``None`` if there is no valid owner.

    A single query joins session to user to submission, so an unauthenticated or
    unowned socket is rejected without a second round trip and without revealing
    whether the submission exists.
    """
    try:
        parsed = uuid.UUID(submission_id)
    except ValueError:
        return None

    factory = get_sessionmaker()
    async with factory() as session:
        row = (
            await session.execute(
                select(Submission.user_id)
                .join(Session, Session.user_id == Submission.user_id)
                .join(User, User.id == Submission.user_id)
                .where(
                    Submission.id == parsed,
                    Session.token_hash == hash_session_token(token),
                    Session.revoked_at.is_(None),
                    User.is_active.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return row


async def _is_finished(*, submission_id: str, user_id: uuid.UUID) -> bool:
    """Whether a submission has left the ``queued`` state."""
    try:
        parsed = uuid.UUID(submission_id)
    except ValueError:
        return True
    factory = get_sessionmaker()
    async with factory() as session:
        status_value = (
            await session.execute(
                select(Submission.status)
                .where(Submission.id == parsed, Submission.user_id == user_id)
                .limit(1)
            )
        ).scalar_one_or_none()
    return status_value is not None and status_value != "queued"


async def _send(websocket: WebSocket, event: dict[str, Any], *, seq: int | None = None) -> None:
    """Send one event frame.

    ``seq`` is advisory: the client uses it for ordering hints and must not drop
    frames on a gap, because a missing frame is lost output and a reordered frame is
    merely late.
    """
    await websocket.send_json(
        {
            "event": event.get("event", "unknown"),
            "data": event.get("data"),
            "seq": seq if seq is not None else 0,
        }
    )
