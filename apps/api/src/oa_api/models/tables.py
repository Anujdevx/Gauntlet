"""SQLAlchemy 2.0 declarative models.

Design decisions that are load-bearing rather than incidental:

**``users.id`` is a UUID, not a bigint.** Submissions are addressed by
``submission_id`` in a URL, a WebSocket path, a Celery message, and a Redis key.
A sequential integer would leak volume and let one candidate enumerate another's
work by incrementing it. UUIDs also let the API mint an id before the row exists,
which is what makes the enqueue-then-persist order in the submission service
possible without a round trip.

**Every table that grades something records ``image_digest``.** A grade is only
meaningful against an exact grader. A mutable image tag means a submission graded
today could have been graded by different hidden tests last month, silently
invalidating the mastery history built from it.

**``submissions.status`` is a string column, not a database enum.** Adding a
terminal state is then a code change plus a migration that alters the check
constraint, rather than an enum migration that PostgreSQL implements awkwardly.
The Python side holds the real enum, so the database cannot be the only place the
set of states is defined.

**Timestamps are always ``timestamptz`` and always UTC.** The platform stores one
candidate's history and may render it in any timezone; naive timestamps make that
a permanent source of off-by-one-day bugs in mastery decay.

**``skill_states`` is keyed (user, node) with an upsert.** Mastery updates arrive
from every submission and must be idempotent under Celery's late acknowledgement:
a redelivered task re-running the same update must not double-apply. The
composite primary key plus ``ON CONFLICT DO UPDATE`` makes that structural.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base with a JSONB-aware type map."""

    # `type_annotation_map` is a plain dict by SQLAlchemy's API contract. It is
    # class-level and mutable only in the sense that a subclass may extend it.
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: JSONB}  # noqa: RUF012


class TimestampMixin:
    """``created_at`` / ``updated_at``, both timezone-aware UTC.

    ``server_default`` rather than a Python-side ``default``, so a row written by
    a migration, a psql session, or an admin tool gets the same timestamps as one
    written by the application.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    """A UUID primary key, generated client-side.

    Client-side so the id exists before the INSERT, which the submission flow
    relies on: it enqueues the Celery message and only then persists, and a
    worker may dequeue before the transaction commits.
    """
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# --------------------------------------------------------------------------
# Enumerations (Python-side; see the module docstring on why not DB enums)
# --------------------------------------------------------------------------


class Difficulty(str, enum.Enum):
    """Question difficulty, used for Elo calibration in Phase 3."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class SubmissionMode(str, enum.Enum):
    """Why a submission was made.

    ``RUN`` executes the candidate's code with no hidden tests present and is
    never graded. ``SUBMIT`` is the graded path. Keeping them distinct means a
    candidate can experiment freely without polluting the mastery signal.
    """

    RUN = "run"
    SUBMIT = "submit"


class QuestionSource(str, enum.Enum):
    """How a served question came to be.

    ``TEMPLATE`` is a curated template served verbatim. ``MUTATED`` is a template
    whose presentation an LLM rewrote -- and which passed validation (reference
    solution still passes, starter code still fails, tests unchanged by hash)
    before being served. Both are graded identically; the distinction exists for
    analytics and for auditing what an LLM actually changed.
    """

    TEMPLATE = "template"
    MUTATED = "mutated"


class InterviewState(str, enum.Enum):
    """Lifecycle of a post-submission interview session."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


class User(Base, TimestampMixin):
    """A candidate.

    Holds no role column yet. Phase 3 adds ``elo_rating`` denormalised here for
    leaderboard queries, but the authoritative value stays in ``elo_ratings`` so
    the history is auditable -- a rating you cannot reconstruct is a rating you
    cannot audit.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    #: Argon2id hash. Never a plaintext or reversible encoding, and never logged.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Cleared on logout. Also bumped on password change so an existing session
    #: cookie cannot survive a credential change.
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # 320 is the RFC 5321 maximum length. Enforced in the column so an
        # over-long address cannot reach the database as an opaque error.
        CheckConstraint("char_length(email) <= 320", name="ck_users_email_length"),
        CheckConstraint("char_length(display_name) >= 1", name="ck_users_display_name_nonempty"),
    )

    submissions: Mapped[list[Submission]] = relationship(back_populates="user")
    skill_states: Mapped[list[SkillState]] = relationship(back_populates="user")


class Session(Base, TimestampMixin):
    """A browser session.

    A table rather than a stateless JWT. Revocation is the reason: a candidate
    who suspects their account is compromised, or an admin disabling an account,
    must be able to invalidate access immediately. A signed token cannot be
    revoked without a denylist, which is a session table with extra steps. The
    token itself is stored hashed, so a database leak does not yield live
    sessions.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: SHA-256 of the opaque token. The plaintext exists only in the cookie.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip_address: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at", "expires_at"),)

    user: Mapped[User] = relationship()


# --------------------------------------------------------------------------
# Concept DNA -- the skill DAG
# --------------------------------------------------------------------------


class ConceptNode(Base, TimestampMixin):
    """A single testable skill, e.g. ``sql.joins.inner``.

    The id is a dotted path rather than a surrogate key because it must be
    stable across environments and across the LLM's mutations: a question
    template, a hidden test, and a candidate's mastery record all refer to the
    same node, and a generated uuid would break that join.
    """

    __tablename__ = "concept_nodes"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Top-level grouping: ``sql``, ``react``, ``python``, ``rag``.
    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: Distance from the domain root. Stored rather than derived because the DAG
    #: is a tree in practice but a graph in principle, and a derived depth would
    #: change meaning the moment someone adds a cross-domain edge.
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("depth >= 0", name="ck_concept_nodes_depth_nonnegative"),
        Index("ix_concept_nodes_domain_depth", "domain", "depth"),
    )


class ConceptEdge(Base, TimestampMixin):
    """A parent-child edge in the skill DAG.

    Acyclicity is enforced by a deferred constraint trigger in the migration
    rather than by application code, because the DAG is written by several
    processes -- the syllabus decomposer, the template seeder, and admins -- and
    a check that only one of them performs is not a check.
    """

    __tablename__ = "concept_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[str] = mapped_column(
        ForeignKey("concept_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    child_id: Mapped[str] = mapped_column(
        ForeignKey("concept_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Relative weight of the parent in the child's mastery. Used by the DAG
    #: blame logic in Phase 3 to distribute a compound failure toward the weaker
    #: contributor.
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    __table_args__ = (
        UniqueConstraint("parent_id", "child_id", name="uq_concept_edges_pair"),
        CheckConstraint("parent_id <> child_id", name="ck_concept_edges_no_self_loop"),
        CheckConstraint("weight > 0", name="ck_concept_edges_weight_positive"),
    )


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------


class QuestionTemplate(Base, TimestampMixin):
    """A curated question with its hidden tests and reference solution.

    ``hidden_tests`` and ``reference_solution`` are the two columns a candidate
    must never see. They live here rather than in ``/templates`` YAML files
    because Phase 3 needs to embed the template text for pgvector similarity
    search, and a source of truth that exists in two places is a source of truth
    that will disagree.
    """

    __tablename__ = "question_templates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    difficulty: Mapped[str] = mapped_column(
        String(16), nullable=False, default=Difficulty.MEDIUM.value
    )
    #: Files the candidate starts with.
    starter_files: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Hidden tests, keyed by relative path. Never serialised to a client.
    hidden_tests: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Reference solution, used by Phase 3 to validate LLM mutations. Never
    #: serialised to a client.
    reference_solution: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Command the sandbox runner executes.
    run_command: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: Seconds the reference solution takes. Used to skip mutation candidates that
    #: would blow the latency budget, not just to grade them as passing.
    reference_runtime_ms: Mapped[int | None] = mapped_column(Integer)
    #: SHA-256 over the sorted hidden-test contents. Phase 3 asserts an LLM
    #: mutation left this unchanged; if the hash moves, the mutation was not
    #: presentation-only and the question is rejected.
    hidden_tests_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: 1536-dimension embedding for pgvector similarity retrieval. Nullable so a
    #: template can be inserted before it is embedded, and the partial index below
    #: only covers embedded rows.
    embedding: Mapped[Any | None] = mapped_column(Vector(1536))
    #: Elo difficulty, calibrated in Phase 3. Initialised to a neutral 1000 so an
    #: uncalibrated template is not treated as either easy or hard.
    elo_difficulty: Mapped[float] = mapped_column(Float, nullable=False, default=1000.0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint(
            "elo_difficulty >= 0 AND elo_difficulty <= 4000",
            name="ck_question_templates_elo_range",
        ),
        # ivfflat needs a lists parameter at build time and a minimum row count to
        # be useful at query time. Partial, so unembedded templates do not degrade
        # the index. Built in the migration, not here: an index on a vector column
        # is a physical structure whose parameters are an operational decision.
        Index(
            "ix_question_templates_embedding_ivfflat",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100, "ivfflat.probes": 10},
            postgresql_where=text("embedding IS NOT NULL"),
        ),
        Index("ix_question_templates_active_lang", "is_active", "language"),
    )


class QuestionTemplateNode(Base):
    """Many-to-many between templates and the concept nodes they assess.

    A separate table rather than a JSONB array because Phase 3 queries "all
    templates covering the lowest-mastery node", which a join serves and a JSONB
    scan does not.
    """

    __tablename__ = "question_template_nodes"

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_templates.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("concept_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    #: This template's weight in assessing the node. A template touching five
    #: nodes should not move each of them as much as one touching a single node.
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    __table_args__ = (
        CheckConstraint("weight > 0", name="ck_template_nodes_weight_positive"),
        Index("ix_template_nodes_node", "node_id"),
    )


class GeneratedQuestion(Base, TimestampMixin):
    """A concrete instance served to a candidate.

    Distinct from the template because a served question is immutable history:
    the exact text, the exact starter files, and the exact tests the candidate was
    graded against. Rewriting the template later must not change what an old
    submission meant.
    """

    __tablename__ = "generated_questions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("question_templates.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=QuestionSource.TEMPLATE.value
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Presentation as served. For a mutated question this differs from the
    #: template; the tests are identical, which the hash proves.
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    starter_files: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Snapshot of the tests hash at serve time, so validation of the served
    #: question does not depend on the template still being unmodified.
    hidden_tests_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Set when the LLM's rewrite was rejected by validation. Kept for auditing
    #: how often mutation succeeds, which is the metric that decides whether the
    #: feature is worth its cost.
    mutation_rejected_reason: Mapped[str | None] = mapped_column(Text)
    served_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_generated_questions_user_served", "user_id", "served_at"),)


# --------------------------------------------------------------------------
# Submissions and grading
# --------------------------------------------------------------------------


class Submission(Base, TimestampMixin):
    """One execution of one candidate's code.

    ``status`` is duplicated from the Celery result rather than read from Redis,
    because a submission's terminal state is durable history: Redis is a cache
    with an expiry, and mastery history must not evaporate when a TTL lapses.
    """

    __tablename__ = "submissions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_questions.id", ondelete="SET NULL"), index=True
    )
    challenge_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SubmissionMode.SUBMIT.value
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    #: Empty for ``run`` mode, whose whole point is that no tests were present.
    files: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: Digest of the exact grader image. The reproducibility anchor.
    image_digest: Mapped[str | None] = mapped_column(String(128))
    #: Which runtime produced the result: ``runc`` or ``runsc``. Recorded because
    #: a runc-graded submission is not comparable to a runsc-graded one, and
    #: without this the two would be indistinguishable after the fact.
    sandbox_runtime: Mapped[str | None] = mapped_column(String(16))
    score_percentage: Mapped[float | None] = mapped_column(Float)
    tests_passed: Mapped[int | None] = mapped_column(Integer)
    tests_total: Mapped[int | None] = mapped_column(Integer)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    runtime_ms: Mapped[int | None] = mapped_column(Integer)
    duration_ms_total: Mapped[int | None] = mapped_column(Integer)
    stdout: Mapped[str | None] = mapped_column(Text)
    stderr: Mapped[str | None] = mapped_column(Text)
    stdout_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stderr_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Celery task id, for correlating a submission with a worker log line.
    celery_task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "score_percentage IS NULL OR (score_percentage >= 0 AND score_percentage <= 100)",
            name="ck_submissions_score_range",
        ),
        Index("ix_submissions_user_created", "user_id", "created_at"),
        Index("ix_submissions_status_created", "status", "created_at"),
    )

    user: Mapped[User] = relationship(back_populates="submissions")
    test_results: Mapped[list[TestResult]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class TestResult(Base, TimestampMixin):
    """One hidden test's outcome, stored individually.

    Individual rows rather than a JSONB array because the skill engine attributes
    a failure to a specific concept node, which needs to join on test identity
    across many submissions.
    """

    __tablename__ = "test_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Stable test identifier, e.g. ``sql.joins.inner::returns_matching_rows``.
    test_key: Mapped[str] = mapped_column(String(400), nullable=False)
    node_id: Mapped[str | None] = mapped_column(
        ForeignKey("concept_nodes.id", ondelete="SET NULL"), index=True
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    stack_trace: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("submission_id", "test_key", name="uq_test_results_submission_key"),
        Index("ix_test_results_node_passed", "node_id", "passed"),
    )

    submission: Mapped[Submission] = relationship(back_populates="test_results")


# --------------------------------------------------------------------------
# Probabilistic skill state
# --------------------------------------------------------------------------


class SkillState(Base, TimestampMixin):
    """A candidate's mastery of one Concept DNA node.

    ``p_mastery`` is the belief that the candidate has learned the skill, in
    [0, 1] -- not a score. It is explicitly allowed to represent uncertainty, and
    that is the whole reason to use BKT instead of a running average: a single
    correct answer to an easy question should not read as mastery, and a single
    wrong answer to a hard one should not read as ignorance.

    BKT parameters are stored per (user, node) rather than shared, because the
    point of BKT is that slip and guess rates are *learned per candidate*. Two
    candidates answering the same question can warrant very different updates.
    """

    __tablename__ = "skill_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(
        ForeignKey("concept_nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    p_mastery: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Probability the candidate already knew the skill before the question.
    p_known: Mapped[float] = mapped_column(Float, nullable=False, default=0.1)
    #: Transition probability: learning on a failed attempt.
    p_transit: Mapped[float] = mapped_column(Float, nullable=False, default=0.10)
    #: Probability of a correct answer given the skill is *not* mastered. Captures
    #: guessing.
    p_guess: Mapped[float] = mapped_column(Float, nullable=False, default=0.20)
    #: Probability of a slip given the skill *is* mastered. Captures carelessness.
    p_slip: Mapped[float] = mapped_column(Float, nullable=False, default=0.10)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "node_id", name="uq_skill_states_user_node"),
        CheckConstraint("p_mastery >= 0 AND p_mastery <= 1", name="ck_skill_states_mastery_range"),
        # Probabilities must be in [0,1]. Without this a bad update writes a
        # negative guess rate, and the BKT posterior silently becomes nonsense
        # rather than raising.
        CheckConstraint(
            "p_known >= 0 AND p_known <= 1"
            " AND p_transit >= 0 AND p_transit <= 1"
            " AND p_guess >= 0 AND p_guess <= 1"
            " AND p_slip >= 0 AND p_slip <= 1",
            name="ck_skill_states_probability_ranges",
        ),
        CheckConstraint("attempts >= 0 AND correct_attempts >= 0", name="ck_skill_states_counts"),
        CheckConstraint("correct_attempts <= attempts", name="ck_skill_states_correct_le_attempts"),
    )

    user: Mapped[User] = relationship(back_populates="skill_states")


class EloRating(Base, TimestampMixin):
    """One rating event, not just the current value.

    An append-only log. A candidate's ability estimate needs to be reconstructible
    -- to explain why the platform served the question it did, and to recompute a
    rating after a fix to the update rule. A single mutable number cannot do either.
    """

    __tablename__ = "elo_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rating_before: Mapped[float] = mapped_column(Float, nullable=False)
    rating_after: Mapped[float] = mapped_column(Float, nullable=False)
    delta: Mapped[float] = mapped_column(Float, nullable=False)
    #: Expected score before the update, retained for calibration analysis.
    expected_score: Mapped[float] = mapped_column(Float, nullable=False)
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("submissions.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="submission")

    __table_args__ = (
        CheckConstraint(
            "rating_before >= 0 AND rating_before <= 4000"
            " AND rating_after >= 0 AND rating_after <= 4000",
            name="ck_elo_ratings_range",
        ),
    )


class InterviewSession(Base, TimestampMixin):
    """A post-submission interview.

    Explicitly unable to affect a grade. There is no ``passed`` column and no
    foreign key from ``submissions`` pointing here, so it is structurally
    impossible for interview feedback to change a pass/fail outcome. That is the
    product's central promise, and encoding it as the absence of a column is
    stronger than encoding it as a comment.
    """

    __tablename__ = "interview_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=InterviewState.PENDING.value, index=True
    )
    #: Questions asked, as a JSONB list of {dimension, prompt, answer}.
    transcript: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    #: LLM scores for clarity, accuracy, depth, each 0-100.
    clarity_score: Mapped[float | None] = mapped_column(Float)
    accuracy_score: Mapped[float | None] = mapped_column(Float)
    depth_score: Mapped[float | None] = mapped_column(Float)
    feedback: Mapped[str | None] = mapped_column(Text)
    #: Model and prompt version, so feedback can be re-audited later.
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'in_progress', 'complete', 'failed')",
            name="ck_interview_sessions_state",
        ),
        UniqueConstraint("submission_id", name="uq_interview_sessions_submission"),
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
]
