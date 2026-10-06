"""Initial schema: identity, Concept DNA DAG, questions, submissions, skill state

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-03

Everything in §9 of the specification, in one migration. It is one migration
rather than nine because nothing here has been deployed: splitting a first
schema into nine revisions produces nine files that are never independently
useful, and a reviewer has to read all of them to understand the shape anyway.

Acyclicity of ``concept_edges`` is enforced by a constraint trigger rather than
by application code. The DAG is written by the syllabus decomposer, the template
seeder, and human admins, and a check only one of them performs is not a check.
The trigger is DEFERRABLE INITIALLY DEFERRED because acyclicity is a property of
the *final* state of a transaction: a topological insertion legitimately inserts
child-before-parent edges, which are momentarily cyclic on their own.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the full schema, plus the DAG acyclicity guard."""
    # -- extensions ---------------------------------------------------------
    # pgvector backs template-embedding similarity search. Created here as well as
    # in the compose init script: a database restored from a dump, or a managed
    # instance, will not have run that script.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # -- identity -----------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("char_length(email) <= 320", name="ck_users_email_length"),
        sa.CheckConstraint("char_length(display_name) >= 1", name="ck_users_display_name_nonempty"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The token is stored hashed, so a database leak does not yield live
        # sessions. 64 hex characters is exactly a SHA-256 digest.
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(400), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_sessions_user_active", "sessions", ["user_id", "revoked_at", "expires_at"])

    # -- Concept DNA --------------------------------------------------------
    op.create_table(
        "concept_nodes",
        sa.Column("id", sa.String(160), primary_key=True, nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("depth >= 0", name="ck_concept_nodes_depth_nonnegative"),
    )
    op.create_index("ix_concept_nodes_domain", "concept_nodes", ["domain"])
    op.create_index("ix_concept_nodes_domain_depth", "concept_nodes", ["domain", "depth"])

    op.create_table(
        "concept_edges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "parent_id",
            sa.String(160),
            sa.ForeignKey("concept_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "child_id",
            sa.String(160),
            sa.ForeignKey("concept_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("parent_id", "child_id", name="uq_concept_edges_pair"),
        sa.CheckConstraint("parent_id <> child_id", name="ck_concept_edges_no_self_loop"),
        sa.CheckConstraint("weight > 0", name="ck_concept_edges_weight_positive"),
    )
    op.create_index("ix_concept_edges_parent_id", "concept_edges", ["parent_id"])
    op.create_index("ix_concept_edges_child_id", "concept_edges", ["child_id"])

    # -- Acyclicity guard ---------------------------------------------------
    # A recursive CTE walks parent -> child from the edge being inserted. If it
    # can reach the new parent, the new edge closes a cycle. Cost is O(V+E) per
    # insert, which is fine at a DAG of a few thousand nodes and is the only way
    # to make the guarantee hold for every writer.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION concept_edges_forbid_cycle()
        RETURNS TRIGGER AS $$
        BEGIN
            IF EXISTS (
                WITH RECURSIVE descendants AS (
                    SELECT child_id FROM concept_edges WHERE parent_id = NEW.child_id
                    UNION
                    SELECT e.child_id
                    FROM concept_edges e
                    JOIN descendants d ON e.parent_id = d.child_id
                )
                SELECT 1 FROM descendants WHERE child_id = NEW.parent_id
            ) THEN
                RAISE EXCEPTION
                    'concept_edges would create a cycle: % -> %',
                    NEW.parent_id, NEW.child_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER concept_edges_acyclic
        AFTER INSERT OR UPDATE ON concept_edges
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION concept_edges_forbid_cycle();
        """
    )

    # -- questions ----------------------------------------------------------
    op.create_table(
        "question_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(160), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("difficulty", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("starter_files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hidden_tests", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reference_solution", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_command", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reference_runtime_ms", sa.Integer(), nullable=True),
        sa.Column("hidden_tests_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("elo_difficulty", sa.Float(), nullable=False, server_default="1000.0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "elo_difficulty >= 0 AND elo_difficulty <= 4000",
            name="ck_question_templates_elo_range",
        ),
    )
    op.create_index("ix_question_templates_slug", "question_templates", ["slug"], unique=True)
    op.create_index("ix_question_templates_language", "question_templates", ["language"])
    op.create_index(
        "ix_question_templates_hidden_tests_hash", "question_templates", ["hidden_tests_hash"]
    )
    op.create_index(
        "ix_question_templates_active_lang", "question_templates", ["is_active", "language"]
    )
    # ivfflat needs `lists` at build time and a minimum row count to be useful at
    # query time. Partial so unembedded templates do not dilute the index.
    op.execute(
        """
        CREATE INDEX ix_question_templates_embedding_ivfflat
        ON question_templates USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
        WHERE embedding IS NOT NULL
        """
    )

    op.create_table(
        "question_template_nodes",
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("question_templates.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.String(160),
            sa.ForeignKey("concept_nodes.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.CheckConstraint("weight > 0", name="ck_template_nodes_weight_positive"),
    )
    op.create_index("ix_template_nodes_node_id", "question_template_nodes", ["node_id"])

    op.create_table(
        "generated_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("question_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), nullable=False, server_default="template"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("starter_files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("difficulty", sa.String(16), nullable=False),
        sa.Column("hidden_tests_hash", sa.String(64), nullable=False),
        sa.Column("mutation_rejected_reason", sa.Text(), nullable=True),
        sa.Column(
            "served_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_generated_questions_template_id", "generated_questions", ["template_id"])
    op.create_index("ix_generated_questions_user_id", "generated_questions", ["user_id"])
    op.create_index(
        "ix_generated_questions_user_served", "generated_questions", ["user_id", "served_at"]
    )

    # -- submissions --------------------------------------------------------
    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generated_questions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("challenge_id", sa.String(160), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="submit"),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # The reproducibility anchor: which exact grader produced this verdict.
        sa.Column("image_digest", sa.String(128), nullable=True),
        # Which runtime did it. A runc-graded submission is not comparable to a
        # runsc-graded one, and without this the two are indistinguishable later.
        sa.Column("sandbox_runtime", sa.String(16), nullable=True),
        sa.Column("score_percentage", sa.Float(), nullable=True),
        sa.Column("tests_passed", sa.Integer(), nullable=True),
        sa.Column("tests_total", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("runtime_ms", sa.Integer(), nullable=True),
        sa.Column("duration_ms_total", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column(
            "stdout_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "stderr_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("celery_task_id", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "score_percentage IS NULL OR (score_percentage >= 0 AND score_percentage <= 100)",
            name="ck_submissions_score_range",
        ),
    )
    op.create_index("ix_submissions_user_id", "submissions", ["user_id"])
    op.create_index("ix_submissions_question_id", "submissions", ["question_id"])
    op.create_index("ix_submissions_challenge_id", "submissions", ["challenge_id"])
    op.create_index("ix_submissions_status", "submissions", ["status"])
    op.create_index("ix_submissions_celery_task_id", "submissions", ["celery_task_id"])
    op.create_index("ix_submissions_user_created", "submissions", ["user_id", "created_at"])
    op.create_index("ix_submissions_status_created", "submissions", ["status", "created_at"])

    op.create_table(
        "test_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_key", sa.String(400), nullable=False),
        sa.Column(
            "node_id",
            sa.String(160),
            sa.ForeignKey("concept_nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("stack_trace", sa.Text(), nullable=True),
        sa.UniqueConstraint("submission_id", "test_key", name="uq_test_results_submission_key"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_test_results_submission_id", "test_results", ["submission_id"])
    op.create_index("ix_test_results_node_id", "test_results", ["node_id"])
    op.create_index("ix_test_results_node_passed", "test_results", ["node_id", "passed"])

    # -- probabilistic skill state -----------------------------------------
    op.create_table(
        "skill_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.String(160),
            sa.ForeignKey("concept_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("p_mastery", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("p_known", sa.Float(), nullable=False, server_default="0.1"),
        sa.Column("p_transit", sa.Float(), nullable=False, server_default="0.10"),
        sa.Column("p_guess", sa.Float(), nullable=False, server_default="0.20"),
        sa.Column("p_slip", sa.Float(), nullable=False, server_default="0.10"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "node_id", name="uq_skill_states_user_node"),
        sa.CheckConstraint(
            "p_mastery >= 0 AND p_mastery <= 1", name="ck_skill_states_mastery_range"
        ),
        # Without these a bad update writes a negative guess rate and the BKT
        # posterior silently becomes nonsense rather than raising.
        sa.CheckConstraint(
            "p_known >= 0 AND p_known <= 1"
            " AND p_transit >= 0 AND p_transit <= 1"
            " AND p_guess >= 0 AND p_guess <= 1"
            " AND p_slip >= 0 AND p_slip <= 1",
            name="ck_skill_states_probability_ranges",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND correct_attempts >= 0", name="ck_skill_states_counts"
        ),
        sa.CheckConstraint(
            "correct_attempts <= attempts", name="ck_skill_states_correct_le_attempts"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_skill_states_user_id", "skill_states", ["user_id"])
    op.create_index("ix_skill_states_node_id", "skill_states", ["node_id"])

    op.create_table(
        "elo_ratings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating_before", sa.Float(), nullable=False),
        sa.Column("rating_after", sa.Float(), nullable=False),
        sa.Column("delta", sa.Float(), nullable=False),
        # Retained for calibration analysis: a rating rule that cannot explain its
        # own expected scores cannot be debugged.
        sa.Column("expected_score", sa.Float(), nullable=False),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submissions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(64), nullable=False, server_default="submission"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "rating_before >= 0 AND rating_before <= 4000"
            " AND rating_after >= 0 AND rating_after <= 4000",
            name="ck_elo_ratings_range",
        ),
    )
    op.create_index("ix_elo_ratings_user_id", "elo_ratings", ["user_id"])
    op.create_index("ix_elo_ratings_submission_id", "elo_ratings", ["submission_id"])

    # -- interview mode -----------------------------------------------------
    op.create_table(
        "interview_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("transcript", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("clarity_score", sa.Float(), nullable=True),
        sa.Column("accuracy_score", sa.Float(), nullable=True),
        sa.Column("depth_score", sa.Float(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.UniqueConstraint("submission_id", name="uq_interview_sessions_submission"),
        sa.CheckConstraint(
            "state IN ('pending', 'in_progress', 'complete', 'failed')",
            name="ck_interview_sessions_state",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_interview_sessions_user_id", "interview_sessions", ["user_id"])
    op.create_index("ix_interview_sessions_submission_id", "interview_sessions", ["submission_id"])
    op.create_index("ix_interview_sessions_state", "interview_sessions", ["state"])


def downgrade() -> None:
    """Drop everything.

    Order matters: child tables first, and the acyclicity trigger before
    ``concept_edges`` because dropping the table removes its triggers implicitly
    but leaving the function behind would leave a name in ``pg_proc`` that a later
    migration might collide with.
    """
    op.execute("DROP TRIGGER IF EXISTS concept_edges_acyclic ON concept_edges")
    op.execute("DROP FUNCTION IF EXISTS concept_edges_forbid_cycle()")

    for table in (
        "interview_sessions",
        "elo_ratings",
        "skill_states",
        "test_results",
        "submissions",
        "generated_questions",
        "question_template_nodes",
        "question_templates",
        "concept_edges",
        "concept_nodes",
        "sessions",
        "users",
    ):
        op.drop_table(table)
