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
    # -- Java --------------------------------------------------------------
    ("java", "Java", "Core Java fluency."),
    ("java.collections", "Collections", "Lists, sets, maps and their idioms."),
    ("java.collections.maps", "Maps", "HashMap and TreeMap: counting, merging, ordering."),
    ("java.streams", "Streams", "Declarative pipelines with java.util.stream."),
    ("java.strings", "Strings", "Parsing, StringBuilder and formatting."),
    (
        "java.oop",
        "Object-oriented design",
        "Classes, interfaces, immutability, equals and hashCode.",
    ),
    ("java.exceptions", "Exceptions", "Checked and unchecked exceptions, custom types."),
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
        # Four scenarios on different data, so a hard-coded answer cannot pass. Each
        # method is renamed to its scenario so results read well in the UI.
        "test_query.py": (
            "from harness import build_case\n"
            "\n"
            "DDL = [\n"
            "    'CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)',\n"
            "    'CREATE TABLE orders (id INTEGER PRIMARY KEY, '\n"
            "     'customer_id INTEGER REFERENCES customers(id), total INTEGER NOT NULL)',\n"
            "]\n"
            "\n"
            "Scenario0 = build_case(\n"
            "    ddl=DDL,\n"
            "    seed=[\n"
            "        \"INSERT INTO customers (id, name) VALUES (1, 'ada'), (2, 'grace')\",\n"
            "        'INSERT INTO orders (id, customer_id, total) VALUES (10, 1, 250), (11, 2, 90), (12, 1, 400)',\n"  # noqa: E501
            "    ],\n"
            "    candidate_sql='',\n"
            "    expectations=[('orders ranked by total, highest first',\n"
            "                   [('ada', 400), ('ada', 250), ('grace', 90)], True)],\n"
            ")\n"
            "Scenario0.test_orders_ranked_by_total_highest_first = Scenario0.test_case_0\n"
            "del Scenario0.test_case_0\n"
            "\n"
            "Scenario1 = build_case(\n"
            "    ddl=DDL,\n"
            "    seed=[\n"
            "        \"INSERT INTO customers (id, name) VALUES (1, 'ada')\",\n"
            "        'INSERT INTO orders (id, customer_id, total) VALUES (10, 1, 100), (11, NULL, 500)',\n"  # noqa: E501
            "    ],\n"
            "    candidate_sql='',\n"
            "    expectations=[('an order with no customer is excluded', [('ada', 100)], True)],\n"
            ")\n"
            "Scenario1.test_an_order_with_no_customer_is_excluded = Scenario1.test_case_0\n"
            "del Scenario1.test_case_0\n"
            "\n"
            "Scenario2 = build_case(\n"
            "    ddl=DDL,\n"
            "    seed=[\n"
            "        \"INSERT INTO customers (id, name) VALUES (1, 'ada'), (2, 'grace'), (3, 'linus')\",\n"  # noqa: E501
            "        'INSERT INTO orders (id, customer_id, total) VALUES (10, 2, 70), (11, 1, 30), (12, 2, 20)',\n"  # noqa: E501
            "    ],\n"
            "    candidate_sql='',\n"
            "    expectations=[('a customer without orders does not appear',\n"
            "                   [('grace', 70), ('ada', 30), ('grace', 20)], True)],\n"
            ")\n"
            "Scenario2.test_a_customer_without_orders_does_not_appear = Scenario2.test_case_0\n"
            "del Scenario2.test_case_0\n"
            "\n"
            "Scenario3 = build_case(\n"
            "    ddl=DDL,\n"
            "    seed=[\n"
            "        \"INSERT INTO customers (id, name) VALUES (7, 'sam')\",\n"
            "        'INSERT INTO orders (id, customer_id, total) VALUES (1, 7, 5)',\n"
            "    ],\n"
            "    candidate_sql='',\n"
            "    expectations=[('a single order', [('sam', 5)], True)],\n"
            ")\n"
            "Scenario3.test_a_single_order = Scenario3.test_case_0\n"
            "del Scenario3.test_case_0\n"
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
            "  throw new Error('Not implemented');\n"
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

JAVA_TEMPLATE: dict[str, Any] = {
    "slug": "java-top-k-words",
    "title": "Most frequent words",
    "summary": (
        "Implement `Solution.topWords(String text, int k)` returning the `k` most "
        "frequent words in `text`.\n\n"
        "- A word is a maximal run of ASCII letters (`a-z`, `A-Z`). Every other "
        "character, including digits, apostrophes and punctuation, separates words.\n"
        "- Words are compared case-insensitively and returned in lower case.\n"
        "- Order by frequency, highest first. Break ties alphabetically.\n"
        "- If there are fewer than `k` distinct words, return all of them. If `k` is "
        "zero or negative, or `text` is empty or `null`, return an empty list."
    ),
    "language": "java",
    "difficulty": "medium",
    "run_command": ["/bin/sh", "/runner/run-java.sh"],
    "starter_files": {
        "Solution.java": r"""import java.util.List;

public class Solution {
    /**
     * Returns the {@code k} most frequent words in {@code text}, most frequent first,
     * ties broken alphabetically. See the task description for the exact rules.
     */
    public static List<String> topWords(String text, int k) {
        throw new UnsupportedOperationException("TODO");
    }
}
"""
    },
    "hidden_tests": {
        "SolutionTest.java": r"""import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class SolutionTest {
    @Test
    void emptyAndNullTextGiveAnEmptyList() {
        assertEquals(List.of(), Solution.topWords("", 3));
        assertEquals(List.of(), Solution.topWords(null, 3));
    }

    @ParameterizedTest
    @CsvSource({"0", "-1", "-100"})
    void nonPositiveKGivesAnEmptyList(int k) {
        assertEquals(List.of(), Solution.topWords("a a b", k));
    }

    @Test
    void wordsAreCaseInsensitiveAndReturnedLowerCase() {
        assertEquals(List.of("go"), Solution.topWords("Go GO go gO", 1));
    }

    @Test
    void tiesAreBrokenAlphabetically() {
        assertEquals(List.of("apple", "pear"), Solution.topWords("pear apple pear apple fig", 2));
    }

    @Test
    void digitsAndPunctuationSeparateWords() {
        assertEquals(List.of("don", "t"), Solution.topWords("don't don't 42 don't", 2));
    }

    @Test
    void fewerDistinctWordsThanKReturnsThemAll() {
        assertEquals(List.of("b", "a"), Solution.topWords("b b a", 10));
    }

    @ParameterizedTest
    @CsvSource(delimiter = '|', value = {
        "the cat the dog the cat|2|the,cat",
        "x y z|3|x,y,z",
        "Z z a A b|2|a,z",
        "one, two; two? three: three: three!|3|three,two,one"
    })
    void rankingsFollowFrequencyThenAlphabet(String text, int k, String expected) {
        assertEquals(Arrays.asList(expected.split(",")), Solution.topWords(text, k));
    }
}
"""
    },
    "reference_solution": {
        "Solution.java": r"""import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

public class Solution {
    public static List<String> topWords(String text, int k) {
        if (text == null || k <= 0) {
            return List.of();
        }
        Map<String, Integer> counts = new HashMap<>();
        StringBuilder word = new StringBuilder();
        for (int i = 0; i <= text.length(); i++) {
            char c = i < text.length() ? text.charAt(i) : ' ';
            boolean letter = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
            if (letter) {
                word.append(Character.toLowerCase(c));
            } else if (word.length() > 0) {
                counts.merge(word.toString(), 1, Integer::sum);
                word.setLength(0);
            }
        }
        return counts.entrySet().stream()
            .sorted(Map.Entry.<String, Integer>comparingByValue().reversed()
                .thenComparing(Map.Entry.comparingByKey()))
            .limit(k)
            .map(Map.Entry::getKey)
            .collect(Collectors.toList());
    }
}
"""
    },
}

TEMPLATES: list[dict[str, Any]] = [PYTHON_TEMPLATE, SQL_TEMPLATE, REACT_TEMPLATE, JAVA_TEMPLATE]

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
    "java-top-k-words": ["java", "java.collections", "java.collections.maps", "java.strings"],
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
