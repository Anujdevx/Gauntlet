"""Password hashing, session tokens, and the auth cookie.

Three choices, each with a reason that outlives the code:

**Argon2id, not bcrypt or PBKDF2.** Argon2id is memory-hard, so a leaked hash
table is expensive to attack with GPUs. bcrypt's 72-byte input limit silently
truncates longer passwords, and PBKDF2's cost is CPU-only, which parallelises
well. Parameters are explicit rather than ``argon2-cffi``'s library defaults,
because a library default can change between releases and silently weaken every
hash ever written with it.

**Opaque random session tokens, stored hashed.** Not JWTs. A signed token cannot
be revoked without a denylist, which is a session table wearing a disguise -- and
revocation is a requirement, not a nicety: a candidate who suspects compromise, or
an admin disabling an account, must lose access immediately.

**The cookie is ``SameSite=Lax``, not ``Strict``.** ``Strict`` would break the
WebSocket upgrade after a cross-site navigation to the app, and the submission
stream is the product. ``Lax`` still blocks cross-site POST, which is the
CSRF-relevant case, so the weaker setting costs nothing here.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

__all__ = [
    "SESSION_COOKIE_NAME",
    "hash_password",
    "hash_session_token",
    "needs_rehash",
    "new_session_token",
    "session_expiry",
    "tokens_match",
    "verify_password",
]

#: Cookie name for the session. Prefixed to avoid colliding with anything the
#: frontend or an analytics script might set on the same origin.
SESSION_COOKIE_NAME: Final[str] = "oa_session"

#: Argon2id parameters, stated rather than inherited.
#:
#: 64 MiB and 3 passes is roughly 50-100 ms on commodity hardware, which is the
#: usual target: slow enough to make an offline attack expensive, fast enough that
#: a login does not feel broken. Raising memory later is safe (it only affects new
#: hashes); lowering it would silently weaken existing ones, so it is pinned.
ARGON2_TIME_COST: Final[int] = 3
ARGON2_MEMORY_COST_KIB: Final[int] = 64 * 1024
ARGON2_PARALLELISM: Final[int] = 2

#: How long a session lasts without re-login. Long enough to work through a timed
#: assessment, short enough that an abandoned machine does not keep access.
SESSION_TTL_HOURS: Final[int] = 12

_hasher = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    """Hash a candidate's password with Argon2id.

    Args:
        password: The plaintext. Never logged, never persisted.

    Returns:
        A PHC-formatted hash string carrying the parameters used, so a future
        parameter increase can detect a weaker stored hash and upgrade it on the
        next login.
    """
    return _hasher.hash(password)


def verify_password(stored_hash: str, candidate_password: str) -> bool:
    """Check a password against a stored hash.

    Returns ``False`` rather than raising on a mismatch, because "wrong password"
    and "no such user" must be indistinguishable to a caller -- otherwise this
    function is a user-enumeration oracle.
    """
    try:
        _hasher.verify(stored_hash, candidate_password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return True


def needs_rehash(stored_hash: str) -> bool:
    """Whether a stored hash predates the current Argon2 parameters.

    Checked on successful login so a parameter increase applies to existing users
    gradually, on their next sign-in, rather than requiring a password reset.
    """
    return _hasher.check_needs_rehash(stored_hash)


def new_session_token() -> str:
    """Mint an opaque session token.

    32 bytes from ``secrets`` -- a CSPRNG, not ``random``. The token carries no
    claims and no signature; it is a lookup key, so there is nothing to forge.
    """
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash a session token for storage.

    SHA-256, not Argon2. Argon2 is for low-entropy secrets an attacker can guess;
    a 256-bit random token cannot be brute-forced, so the only threat worth
    defending against is a database leak, and SHA-256 prevents that at negligible
    cost. Argon2 here would add ~100 ms to every authenticated request for no
    security gain.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(candidate_token: str, stored_hash: str) -> bool:
    """Constant-time comparison of a presented token against a stored hash.

    Uses :func:`hmac.compare_digest` so comparison time does not leak how much of
    the hash matched.
    """
    return hmac.compare_digest(hash_session_token(candidate_token), stored_hash)


def session_expiry(*, now: datetime | None = None) -> datetime:
    """Compute the expiry timestamp for a new session.

    Args:
        now: Injectable for tests. Defaults to the current UTC time.

    Returns:
        A timezone-aware datetime :data:`SESSION_TTL_HOURS` in the future.
    """
    moment = now or datetime.now(UTC)
    return moment + timedelta(hours=SESSION_TTL_HOURS)
