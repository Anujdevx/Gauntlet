"""Seed the Concept DNA DAG and a small bank of question templates.

Idempotent: safe to run repeatedly. Everything is keyed on a natural identifier --
a dotted node id, a template slug -- so a re-run updates in place rather than
duplicating. That matters because the schema was seeded once already and seeding
must not be a "drop the database first" operation.

Run with ``make seed``.

What is seeded:

* A four-domain Concept DNA DAG (sql, react, python, rag) with parent-child edges.
  Enough depth to exercise the dashboard's grouping and the Phase 3 DAG blame logic.
* Three question templates, one per language, each with working hidden tests and a
  reference solution. These are not placeholders: the Python one genuinely runs in
  the sandbox and is used by the end-to-end acceptance check.

Deliberately absent: embeddings. Phase 3 generates them, because a zero vector in a
pgvector index is worse than no row -- it matches every cosine query with a
meaningless score. Seeding a placeholder would quietly poison retrieval.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from oa_api.models.db import dispose_engine, session_scope
from oa_api.models.tables import ConceptEdge, ConceptNode, QuestionTemplate, QuestionTemplateNode

# --------------------------------------------------------------------------
# Concept DNA: (node_id, label, description)
# --------------------------------------------------------------------------

NODES: list[tuple[str, str, str]] = [
    # -- SQL ---------------------------------------------------------------
    ("sql", "SQL", "Querying relational data."),
    ("sql.select", "SELECT", "Projection, filtering, and basic expressions."),
    ("sql.joins", "Joins", "Combining rows across related tables."),
    ("sql.joins.inner", "Inner Join", "Rows present in both tables."),
    ("sql.joins.outer", "Outer Join", "Preserving unmatched rows from either side."),
    ("sql.aggregation", "Aggregation", "GROUP BY, HAVING, and aggregate functions."),
    ("sql.ordering", "Ordering", "ORDER BY, LIMIT, and deterministic result sets."),
    # -- React -------------------------------------------------------------
    ("react", "React", "Component composition and rendering."),
    ("react.hooks", "Hooks", "Stateful logic in function components."),
    ("react.hooks.useeffect", "useEffect", "Synchronising with external systems."),
    (
        "react.hooks.useeffect.dependencies",
        "useEffect dependencies",
        "Correctly enumerating dependencies so effects run when they should.",
    ),
    ("react.state", "State", "Choosing state shape and update strategy."),
    ("react.memoization", "Memoization", "useMemo, useCallback, and React.memo."),
    # -- Python ------------------------------------------------------------
    ("python", "Python", "Core language fluency."),
    ("python.asyncio", "asyncio", "Concurrent code with the event loop."),
    ("python.asyncio.tasks", "asyncio tasks", "Creating, scheduling, and cancelling tasks."),
    ("python.iterators", "Iterators", "Lazy evaluation and the iterator protocol."),
    ("python.exceptions", "Exceptions", "Raising, chaining, and handling."),
    # -- RAG ---------------------------------------------------------------
    ("rag", "Retrieval-Augmented Generation", "Grounding generation in retrieved context."),
    ("rag.retrieval", "Retrieval", "Selecting relevant context for a query."),
    ("rag.retrieval.chunking", "Chunking", "Splitting documents so retrieval works."),
    ("rag.assembly", "Prompt assembly", "Placing retrieved context into a prompt."),
    ("rag.context", "Context windows", "Fitting evidence into a finite window."),
]


def _edges() -> list[tuple[str, str, float]]:
    """Parent-child edges implied by each node's dotted id.

    Derived from the ids rather than listed separately, so a node added to
    :data:`NODES` is automatically connected. A separate edge list is a second
    source of truth that will forget the node someone just added.
    """
    known = {node_id for node_id, _, _ in NODES}
    out: list[tuple[str, str, float]] = []
    for node_id, _label, _description in NODES:
        head, sep, _tail = node_id.rpartition(".")
        if sep and head in known:
            out.append((head, node_id, 1.0))
    return out


# --------------------------------------------------------------------------
# Question templates
# --------------------------------------------------------------------------

PYTHON_TEMPLATE: dict[str, Any] = {
    "slug": "python-groupby-streaks",
    "title": "Consecutive run lengths",
    "summary": (
        "A log of events arrives unsorted. Write `longest_streak(events)` returning "
        "the length of the longest run of consecutive equal values.\n\n"
        "A run is a maximal sequence of identical adjacent values. The empty list has "
        "a longest streak of 0.\n\n"
        "Your function should be O(n) in time and O(1) in extra space."
    ),
    "language": "python",
    "difficulty": "medium",
    "run_command": ["/bin/sh", "/runner/run-python.sh"],
    "starter_files": {
        "solution.py": (
            "def longest_streak(events: list[str]) -> int:\n"
            '    """Length of the longest run of identical adjacent values."""\n'
            "    # Implement this. The tests are hidden; they check correctness, not\n"
            "    # output formatting.\n"
            "    raise NotImplementedError\n"
        )
    },
    "hidden_tests": {
        "test_longest_streak.py": (
            "import pytest\n"
            "\n"
            "from solution import longest_streak\n"
            "\n"
            "\n"
            "@pytest.mark.parametrize(\n"
            "    ('events', 'expected'),\n"
            "    [\n"
            "        ([], 0),\n"
            "        (['a'], 1),\n"
            "        (['a', 'a', 'a'], 3),\n"
            "        (['a', 'b', 'a'], 1),\n"
            "        (['a', 'a', 'b', 'b', 'b', 'c'], 3),\n"
            "        (list('abababab'), 1),\n"
            "    ],\n"
            ")\n"
            "def test_longest_streak(events, expected):\n"
            "    assert longest_streak(events) == expected\n"
            "\n"
            "\n"
            "def test_does_not_mutate_input():\n"
            "    events = ['x', 'x', 'y']\n"
            "    longest_streak(events)\n"
            "    assert events == ['x', 'x', 'y']\n"
        )
    },
    "reference_solution": {
        "solution.py": (
            "def longest_streak(events: list[str]) -> int:\n"
            "    best = current = 0\n"
            "    previous = None\n"
            "    for value in events:\n"
            "        current = current + 1 if value == previous else 1\n"
            "        best = max(best, current)\n"
            "        previous = value\n"
            "    return best\n"
        )
    },
}

SQL_TEMPLATE: dict[str, Any] = {
    "slug": "sql-inner-join-orders",
    "title": "Orders with their customers",
    "summary": (
        "Write a query that returns one row per order, with the customer's name and "
        "the order total, ordered by total descending.\n\n"
        "Return exactly the columns `customer_name` and `total`. An order with no "
        "matching customer must not appear -- use an inner join."
    ),
    "language": "sql",
    "difficulty": "easy",
    "run_command": ["/bin/sh", "/runner/run-sql.sh"],
    "starter_files": {"solution.sql": "-- Write your query here.\n"},
    "hidden_tests": {
        "test_query.py": (
            "from harness import SQLGradingCase, build_case\n"
            "\n"
            "DDL = [\n"
            "    'CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)',\n"
            "    'CREATE TABLE orders (id INTEGER PRIMARY KEY, '\n"
            "     'customer_id INTEGER REFERENCES customers(id), total INTEGER NOT NULL)',\n"
            "]\n"
            "SEED = [\n"
            "    \"INSERT INTO customers (id, name) VALUES (1, 'ada'), (2, 'grace')\",\n"
            "    'INSERT INTO orders (id, customer_id, total) VALUES '\n"
            "     '(10, 1, 250), (11, 2, 90), (12, 1, 400)',\n"
            "]\n"
            "EXPECTED = [('ada', 400), ('ada', 250), ('grace', 90)]\n"
            "\n"
            "GeneratedCase = build_case(\n"
            "    ddl=DDL, seed=SEED, candidate_sql='',\n"
            "    expectations=[('ordered by total desc', EXPECTED, True)],\n"
            ")\n"
        )
    },
    "reference_solution": {
        "solution.sql": (
            "SELECT c.name AS customer_name, o.total AS total\n"
            "FROM orders o\n"
            "JOIN customers c ON c.id = o.customer_id\n"
            "ORDER BY o.total DESC;\n"
        )
    },
}

REACT_TEMPLATE: dict[str, Any] = {
    "slug": "react-useeffect-dependencies",
    "title": "Debounced search input",
    "summary": (
        "Complete `useDebouncedValue(value, delayMs)` so it returns `value` only "
        "after it has stopped changing for `delayMs` milliseconds.\n\n"
        "Every pending timer must be cleared on change and on unmount, so the "
        "component does not update after it is gone."
    ),
    "language": "react",
    "difficulty": "hard",
    "run_command": ["/bin/sh", "/runner/run-react.sh"],
    "starter_files": {
        "useDebouncedValue.ts": (
            "import { useEffect, useState } from 'react';\n"
            "\n"
            "export function useDebouncedValue<T>(value: T, delayMs: number): T {\n"
            "  // Implement this.\n"
            "  return value;\n"
            "}\n"
        )
    },
    "hidden_tests": {
        "useDebouncedValue.test.ts": (
            "import { describe, expect, it, vi } from 'vitest';\n"
            "import { act, renderHook } from '@testing-library/react';\n"
            "\n"
            "import { useDebouncedValue } from './useDebouncedValue';\n"
            "\n"
            "describe('useDebouncedValue', () => {\n"
            "  it('returns the initial value immediately', () => {\n"
            "    vi.useFakeTimers();\n"
            "    const { result } = renderHook(() => useDebouncedValue('a', 100));\n"
            "    expect(result.current).toBe('a');\n"
            "    vi.useRealTimers();\n"
            "  });\n"
            "\n"
            "  it('does not update until the delay elapses', () => {\n"
            "    vi.useFakeTimers();\n"
            "    const { result, rerender } = renderHook(\n"
            "      ({ v }) => useDebouncedValue(v, 100),\n"
            "      { initialProps: { v: 'a' } },\n"
            "    );\n"
            "    rerender({ v: 'b' });\n"
            "    expect(result.current).toBe('a');\n"
            "    act(() => { vi.advanceTimersByTime(99); });\n"
            "    expect(result.current).toBe('a');\n"
            "    act(() => { vi.advanceTimersByTime(1); });\n"
            "    expect(result.current).toBe('b');\n"
            "    vi.useRealTimers();\n"
            "  });\n"
            "});\n"
        )
    },
    "reference_solution": {
        "useDebouncedValue.ts": (
            "import { useEffect, useState } from 'react';\n"
            "\n"
            "export function useDebouncedValue<T>(value: T, delayMs: number): T {\n"
            "  const [debounced, setDebounced] = useState(value);\n"
            "  useEffect(() => {\n"
            "    const timer = setTimeout(() => setDebounced(value), delayMs);\n"
            "    return () => clearTimeout(timer);\n"
            "  }, [value, delayMs]);\n"
            "  return debounced;\n"
            "}\n"
        )
    },
}

TEMPLATES: list[dict[str, Any]] = [PYTHON_TEMPLATE, SQL_TEMPLATE, REACT_TEMPLATE]

#: Which concept nodes each template assesses.
TEMPLATE_NODES: dict[str, list[str]] = {
    "python-groupby-streaks": ["python", "python.iterators"],
    "sql-inner-join-orders": ["sql", "sql.joins", "sql.joins.inner", "sql.ordering"],
    "react-useeffect-dependencies": [
        "react",
        "react.hooks",
        "react.hooks.useeffect",
        "react.hooks.useeffect.dependencies",
    ],
}


def hidden_tests_hash(hidden_tests: dict[str, str]) -> str:
    """SHA-256 over the sorted hidden-test contents.

    Phase 3 asserts that an LLM mutation left this unchanged. Hashing the *sorted
    contents* rather than the dict's iteration order makes it stable across
    interpreters, so a rejection means the tests actually changed.
    """
    digest = hashlib.sha256()
    for path in sorted(hidden_tests):
        digest.update(path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hidden_tests[path].encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


async def seed(*, verbose: bool = True) -> dict[str, int]:
    """Insert or update every node, edge, and template.

    Returns:
        Counts of what was written, for the CLI summary and for tests.
    """
    counts = {"nodes": 0, "edges": 0, "templates": 0}

    async with session_scope() as session:
        for node_id, label, description in NODES:
            depth = node_id.count(".")
            domain = node_id.split(".")[0]
            stmt = (
                pg_insert(ConceptNode)
                .values(
                    id=node_id, label=label, domain=domain, depth=depth, description=description
                )
                .on_conflict_do_update(
                    index_elements=[ConceptNode.id],
                    set_={"label": label, "depth": depth, "description": description},
                )
            )
            await session.execute(stmt)
            counts["nodes"] += 1

        for parent, child, weight in _edges():
            stmt = (
                pg_insert(ConceptEdge)
                .values(parent_id=parent, child_id=child, weight=weight)
                .on_conflict_do_update(
                    constraint="uq_concept_edges_pair",
                    set_={"weight": weight},
                )
            )
            await session.execute(stmt)
            counts["edges"] += 1

        for spec in TEMPLATES:
            slug = str(spec["slug"])
            hidden = dict(spec["hidden_tests"])
            stmt = (
                pg_insert(QuestionTemplate)
                .values(
                    slug=slug,
                    title=spec["title"],
                    summary=spec["summary"],
                    language=spec["language"],
                    difficulty=spec["difficulty"],
                    starter_files=dict(spec["starter_files"]),
                    hidden_tests=hidden,
                    reference_solution=dict(spec["reference_solution"]),
                    run_command=list(spec["run_command"]),
                    hidden_tests_hash=hidden_tests_hash(hidden),
                    is_active=True,
                )
                .on_conflict_do_update(
                    index_elements=[QuestionTemplate.slug],
                    set_={
                        "title": spec["title"],
                        "summary": spec["summary"],
                        "starter_files": dict(spec["starter_files"]),
                        "hidden_tests": hidden,
                        "reference_solution": dict(spec["reference_solution"]),
                        "run_command": list(spec["run_command"]),
                        "hidden_tests_hash": hidden_tests_hash(hidden),
                    },
                )
            )
            await session.execute(stmt)
            # `ON CONFLICT DO UPDATE` returns no rowset, so the id is read back
            # rather than taken from RETURNING. Without this the upsert silently
            # links zero templates to concept nodes.
            template_id = await session.scalar(
                select(QuestionTemplate.id).where(QuestionTemplate.slug == slug)
            )

            if template_id is not None:
                for node_id in TEMPLATE_NODES.get(slug, []):
                    link = (
                        pg_insert(QuestionTemplateNode)
                        .values(template_id=template_id, node_id=node_id, weight=1.0)
                        .on_conflict_do_nothing()
                    )
                    await session.execute(link)
            counts["templates"] += 1

    if verbose:
        # stderr, not stdout: stdout is the machine-readable channel, and a
        # summary interleaved into it would break `make seed | jq`.
        print(
            json.dumps(
                {
                    "seeded": counts,
                    "note": "embeddings are left NULL by design; Phase 3 generates them",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
    return counts


async def _main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Seed concept nodes and question templates.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what is present without writing anything.",
    )
    args = parser.parse_args()

    if args.check:
        async with session_scope() as session:
            nodes = len((await session.execute(select(ConceptNode.id))).all())
            templates = len((await session.execute(select(QuestionTemplate.id))).all())
        print(
            json.dumps({"concept_nodes": nodes, "question_templates": templates}, indent=2),
            file=sys.stderr,
        )
        return 0

    await seed()
    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
