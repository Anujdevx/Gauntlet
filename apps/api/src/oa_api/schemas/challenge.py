"""Request and response models for challenges, submissions, and skills.

The central design constraint in this module is stated in
:mod:`oa_api.schemas.execution`: hidden tests and reference solutions have no
field in any response model. Their absence is enforced by
``test_no_response_schema_exposes_hidden_tests``, which walks every model in this
package and fails if one gains a field that could carry them. A comment saying "do
not serialise the hidden tests" is not a control; a test is.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ChallengeDetailResponse",
    "ChallengeEnvelope",
    "ChallengeListResponse",
    "ChallengeResponse",
    "CreateSubmissionRequest",
    "SkillListResponse",
    "SkillNodeResponse",
    "SubmissionAccepted",
    "SubmissionStatusResponse",
]

Language = Literal["python", "react", "sql"]
Difficulty = Literal["easy", "medium", "hard"]
SubmissionMode = Literal["run", "submit"]


class ChallengeResponse(BaseModel):
    """A challenge as listed in the index.

    Carries no starter files or prompt: the index renders dozens of cards and
    shipping every question body for them would be a payload nobody asked for.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    title: str
    summary: str
    language: Language
    difficulty: Difficulty
    concept_nodes: list[str]
    estimated_minutes: int
    tags: list[str]


class ChallengeDetailResponse(ChallengeResponse):
    """A challenge with its body and starting files.

    ``starter_files`` is what the candidate is given. The hidden tests for this
    challenge exist in the database and in the payload, and there is deliberately
    no field here that could carry them.
    """

    prompt: str
    starter_files: dict[str, str]


class ChallengeListResponse(BaseModel):
    challenges: list[ChallengeResponse]


class ChallengeEnvelope(BaseModel):
    """Single-object wrapper.

    Every list and single-object response is wrapped rather than returning a bare
    object, so adding a field later -- a deprecation notice, an ETag, a cursor --
    is not a breaking change. A bare object has nowhere to put them.
    """

    challenge: ChallengeDetailResponse


class CreateSubmissionRequest(BaseModel):
    """A candidate pressing Run or Submit."""

    model_config = ConfigDict(extra="forbid")

    challenge_id: Annotated[str, Field(min_length=1, max_length=160)]
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Candidate-authored file contents, keyed by relative path.",
    )
    #: ``run`` executes without hidden tests present and is never graded.
    #: ``submit`` is the graded path and the only one that updates mastery.
    mode: SubmissionMode = "submit"

    @classmethod
    def size_limits(cls) -> dict[str, int]:
        """Count and byte ceilings, mirrored from the sandbox envelope.

        Duplicated rather than imported so the API can reject an oversized
        submission *before* storing it, without the worker module becoming a
        dependency of the control plane. The values are asserted equal to the
        envelope's by a test, because a limit that drifts between the two layers
        is a limit that only one of them enforces.
        """
        from oa_engine.platform.config import DEFAULT_ENVELOPE

        return {
            "max_files": DEFAULT_ENVELOPE.max_submission_files,
            "max_bytes": DEFAULT_ENVELOPE.max_submission_bytes,
        }


class SubmissionAccepted(BaseModel):
    """The 202 response.

    Returned immediately with an opaque id. It never contains a result, because
    the API has no way to produce one: it holds no Docker socket.
    """

    submission_id: str
    stream_key: str
    status: Literal["queued"] = "queued"
    #: Present so the client can distinguish "accepted" from "we lost it".
    queued_at: datetime


class SubmissionStatusResponse(BaseModel):
    """Polling fallback for the result.

    ``result`` is ``None`` while the submission is still running. A client that
    receives ``status: 'passed'`` with a null result should treat it as a
    retrieval problem, never as an empty passing run.
    """

    submission_id: str
    status: str
    result: dict[str, Any] | None = None
    score_percentage: float | None = None
    finished_at: datetime | None = None


class SkillNodeResponse(BaseModel):
    """One Concept DNA node with the viewer's mastery of it.

    ``mastery`` is a belief in [0, 1], not a score, and ``attempts`` is how many
    observations produced it. A node with mastery 0.9 and 1 attempt means something
    very different from 0.9 and 20, and the UI shows both.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    domain: str
    depth: int
    parent_id: str | None = None
    mastery: float
    attempts: int
    correct_attempts: int = 0
    last_seen_at: datetime | None = None


class SkillListResponse(BaseModel):
    nodes: list[SkillNodeResponse]


class GeneratedQuestionResponse(BaseModel):
    """A served question, as the candidate sees it."""

    id: uuid.UUID
    challenge_id: str
    title: str
    prompt: str
    starter_files: dict[str, str]
    language: Language
    difficulty: Difficulty
    served_at: datetime
