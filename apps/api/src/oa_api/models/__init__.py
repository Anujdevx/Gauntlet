"""SQLAlchemy ORM models and the async engine factory.

Re-exported from :mod:`oa_api.models.tables` so callers import from one place.
"""

from __future__ import annotations

from oa_api.models.db import async_sessionmaker, create_engine_and_session, dispose_engine
from oa_api.models.tables import (
    Base,
    ConceptEdge,
    ConceptNode,
    Difficulty,
    EloRating,
    GeneratedQuestion,
    InterviewSession,
    InterviewState,
    QuestionSource,
    QuestionTemplate,
    QuestionTemplateNode,
    Session,
    SkillState,
    Submission,
    SubmissionMode,
    TestResult,
    User,
)

__all__ = [
    "Base",
    "ConceptEdge",
    "ConceptNode",
    "Difficulty",
    "EloRating",
    "GeneratedQuestion",
    "InterviewSession",
    "InterviewState",
    "QuestionSource",
    "QuestionTemplate",
    "QuestionTemplateNode",
    "Session",
    "SkillState",
    "Submission",
    "SubmissionMode",
    "TestResult",
    "User",
    "async_sessionmaker",
    "create_engine_and_session",
    "dispose_engine",
]
