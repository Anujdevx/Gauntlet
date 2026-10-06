"""Request and response models for authentication.

Pydantic is the API's contract, so these are declared once and imported by the
router. Field-level constraints live here rather than in the router so that the
OpenAPI document, the validation, and the error messages all agree.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

__all__ = [
    "LoginRequest",
    "MeResponse",
    "SignupRequest",
    "UserResponse",
]

#: Deliberately permissive on the local part and strict on the shape. Full RFC
#: 5322 conformance is famously impractical, and over-strict email validation
#: rejects real addresses, which is worse for a candidate than a malformed one
#: that bounces at first login.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class UserResponse(BaseModel):
    """A candidate, as returned to the client.

    Contains no ``password_hash`` and no ``session_version``. Not by omission --
    there is no field to leak, because this model is a separate projection rather
    than a serialised ORM row.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str


class SignupRequest(BaseModel):
    """New-account registration."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: Annotated[str, Field(min_length=12, max_length=256)]
    display_name: Annotated[str, Field(min_length=1, max_length=80)]

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        """Trim and reject a whitespace-only name.

        A minimum length of 1 accepts ``" "``, which then renders as a blank
        display name in the UI and looks like a bug.
        """
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("display_name must not be blank")
        return trimmed

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, v: str) -> str:
        """Lowercase the domain part so case cannot create duplicate accounts.

        The local part is left alone: RFC 5321 says it is case-sensitive, and
        silently folding it would be wrong even though no real mail provider treats
        it that way.
        """
        local, _, domain = v.rpartition("@")
        return f"{local}@{domain.lower()}"


class LoginRequest(BaseModel):
    """Credential check."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=256)]


class MeResponse(BaseModel):
    """The current session's identity."""

    user: UserResponse
    expires_at: datetime
