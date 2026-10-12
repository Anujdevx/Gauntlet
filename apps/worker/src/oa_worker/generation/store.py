"""Persist an accepted question into the bank (``question_templates``)."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from oa_api.models.tables import QuestionTemplate, QuestionTemplateNode
from oa_api.scripts.seed_templates import hidden_tests_hash
from oa_engine.generation import Question

__all__ = ["existing_titles", "save_question", "slug_for"]


def slug_for(question: Question) -> str:
    """Stable, readable, collision-resistant: language + title + tests hash."""
    words = re.sub(r"[^a-z0-9]+", "-", question.title.lower()).strip("-")[:60]
    return f"gen-{question.language}-{words}-{hidden_tests_hash(question.hidden_tests)[:6]}"


def _lineage(node_id: str) -> list[str]:
    """``a.b.c`` -> ``[a, a.b, a.b.c]``: a question exercises its ancestors too."""
    parts = node_id.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


async def existing_titles(
    session: AsyncSession, language: str, *, scope_node: str | None = None
) -> list[str]:
    """Titles already in the bank, so the model does not repeat an idea.

    Scoped to ``scope_node`` (and everything linked under it) when given, otherwise to
    the language. DSA uses the former: a Python and a Java two-pointer question are the
    same idea, so both count as "already covered".
    """
    stmt = select(QuestionTemplate.title).where(QuestionTemplate.is_active.is_(True))
    if scope_node is None:
        stmt = stmt.where(QuestionTemplate.language == language)
    else:
        stmt = stmt.join(
            QuestionTemplateNode, QuestionTemplateNode.template_id == QuestionTemplate.id
        ).where(QuestionTemplateNode.node_id == scope_node)
    return list((await session.scalars(stmt.distinct())).all())


async def save_question(
    session: AsyncSession,
    *,
    question: Question,
    node_id: str,
    difficulty: str,
    reference_runtime_ms: int | None,
) -> str | None:
    """Insert the question and link it to its skill nodes.

    Returns the slug, or ``None`` if an identical test bundle is already in the bank.
    """
    digest = hidden_tests_hash(question.hidden_tests)
    duplicate = await session.scalar(
        select(QuestionTemplate.id).where(QuestionTemplate.hidden_tests_hash == digest)
    )
    if duplicate is not None:
        return None

    slug = slug_for(question)
    statement = (
        pg_insert(QuestionTemplate)
        .values(
            slug=slug,
            title=question.title,
            summary=question.prompt,
            language=question.language,
            difficulty=difficulty,
            starter_files=question.starter_files,
            hidden_tests=question.hidden_tests,
            reference_solution=question.reference_files,
            run_command=list(question.run_command),
            reference_runtime_ms=reference_runtime_ms,
            hidden_tests_hash=digest,
            is_active=True,
        )
        .on_conflict_do_nothing(index_elements=[QuestionTemplate.slug])
    )
    await session.execute(statement)
    template_id = await session.scalar(
        select(QuestionTemplate.id).where(QuestionTemplate.slug == slug)
    )
    if template_id is None:
        return None
    for node in _lineage(node_id):
        await session.execute(
            pg_insert(QuestionTemplateNode)
            .values(template_id=template_id, node_id=node, weight=1.0)
            .on_conflict_do_nothing()
        )
    return slug
