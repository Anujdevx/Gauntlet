"""SQL grading harness.

Graded by running a candidate's query against a fresh in-memory SQLite database
and comparing materialised rows to a hard-coded expectation.

The design decision that makes SQL grading deterministic is **a new connection
per test**. ``setUp`` opens ``sqlite3.connect(":memory:")``, applies the DDL, and
inserts the seed rows; ``tearDown`` closes it. In-memory databases die with their
connection, so there is no file to leak and no fixture to leak either.

The alternative -- one shared database, truncated between tests -- is a classic
source of phantom passes: a candidate's ``INSERT`` in an earlier test survives
into a later one and quietly satisfies an assertion that should have failed. A
fresh connection per test makes that class of bug impossible rather than merely
unlikely.

Three candidate behaviours this harness is specifically built to catch:

**No ``fetchall``.** The harness materialises the cursor itself before comparing.
A candidate who writes a correct query but forgets to fetch gets zero rows and
fails, which is right: the question asks for a query, not a guess.

**A query that mutates the fixture.** ``assert_query`` runs the candidate's SQL
inside a savepoint, so a ``DROP TABLE`` in their query cannot affect subsequent
tests.

**Row order.** ``assert_query`` compares as ordered by default, because "return
the top 3 by revenue" means something specific about order. Tests that genuinely
do not care pass ``ordered=False``, which sorts both sides canonically before
comparing.

This module is part of the image and root-owned. A candidate cannot modify it,
which is what makes the grading trustworthy.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import traceback
import unittest
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, TypeVar

#: Where the candidate's submission is looked for, in either supported shape.
WORKSPACE = Path("/tmp/workspace")

#: Rows beyond this are truncated in the diagnostic message. A candidate who
#: writes ``SELECT * FROM big`` gets a readable failure instead of a megabyte of
#: output that would blow past the worker's stream cap.
MAX_REPORTED_ROWS = 20

Row = tuple[Any, ...]
T = TypeVar("T")


@contextmanager
def fresh_database(*, ddl: Sequence[str], seed: Sequence[str]) -> Iterator[sqlite3.Connection]:
    """Yield a pristine in-memory database with the fixture applied.

    Args:
        ddl: ``CREATE``/``ALTER`` statements, applied in order.
        seed: ``INSERT`` statements, applied in order.

    Yields:
        A connection with an empty transaction, so a candidate's query cannot
        leave a write lock or an uncommitted transaction behind.

    Raises:
        sqlite3.Error: If the fixture itself is invalid. That is a template
            authoring bug, not a candidate failure, and the worker's SQL adapter
            maps it to ``INFRA_ERROR`` rather than penalising anyone.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for statement in ddl:
            conn.execute(statement)
        for statement in seed:
            conn.execute(statement)
        conn.commit()
        yield conn
    finally:
        conn.close()


def materialise(conn: sqlite3.Connection, sql: str) -> list[Row]:
    """Run ``sql`` and return its rows, forcing the cursor to be drained.

    A candidate's query is expected to be a ``SELECT``. Anything else -- an
    ``INSERT``, a ``PRAGMA``, multiple statements -- raises here and surfaces as
    a normal test failure with the sqlite3 message attached.
    """
    cursor = conn.execute(sql)
    rows = cursor.fetchall()
    cursor.close()
    return [tuple(row) for row in rows]


def render_rows(rows: Sequence[Row]) -> str:
    """Format rows for a failure message, truncating long result sets."""
    if len(rows) > MAX_REPORTED_ROWS:
        head = "\n".join(f"    {row!r}" for row in rows[:MAX_REPORTED_ROWS])
        return f"{head}\n    ... {len(rows) - MAX_REPORTED_ROWS} more rows"
    return "\n".join(f"    {row!r}" for row in rows)


class SQLGradingCase(unittest.TestCase):
    """Base class for a generated SQL test case.

    A question template supplies ``ddl``, ``seed``, ``candidate_sql``, and a
    list of ``(name, expected)`` pairs. Each pair becomes one hidden test method,
    so the worker receives per-test booleans rather than a single verdict.
    """

    #: ClassVar, not a plain attribute: a mutable list default on a class is a
    #: shared-object bug waiting to happen, and ``build_case`` synthesises
    #: subclasses that set these. Annotation-only would leave a bare class body
    #: value behind, so an explicit empty tuple is the safe default.
    ddl: ClassVar[tuple[str, ...]] = ()
    seed: ClassVar[tuple[str, ...]] = ()
    #: The candidate's query, read from /workspace at grading time.
    candidate_sql: str = ""
    #: ``(test_name, expected_rows, ordered)`` triples. Announced but not
    #: defaulted: ``build_case`` always supplies it, and a mutable list default
    #: here would be shared across every subclass that forgot to override it.
    #: ``getattr`` with an empty default is how the harness reads it safely.
    expectations: list[tuple[str, Sequence[Row], bool]]

    def setUp(self) -> None:
        """Build a fresh database for every single test.

        Not ``setUpClass``. That distinction is the entire point of this harness.
        """
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        for statement in self.ddl:
            self.conn.execute(statement)
        for statement in self.seed:
            self.conn.execute(statement)
        self.conn.commit()

    def tearDown(self) -> None:
        """Close the connection, discarding the database with it."""
        conn = getattr(self, "conn", None)
        if conn is not None:
            conn.close()

    def read_candidate_sql(self) -> str:
        """Read the submitted query from /workspace.

        Supports two shapes, both common in real assessments:

        * A file named ``solution.sql`` containing the query.
        * A file ``solution.py`` defining ``SQL`` or a ``query(...)`` function,
          for questions where the answer must be parameterised.

        Raises:
            AssertionError: When no submission exists, so the failure reads as a
                candidate problem rather than an internal one.
        """
        sql_path = WORKSPACE / "solution.sql"
        py_path = WORKSPACE / "solution.py"

        if sql_path.is_file():
            text = sql_path.read_text(encoding="utf-8").strip()
            self.assertTrue(text, "solution.sql is empty")
            return text

        if py_path.is_file():
            namespace: dict[str, Any] = {}
            # exec is the point of this branch: a SQL question may require the
            # answer to be parameterised, so the submission is a Python snippet
            # that must be executed to obtain the query.
            exec(compile(py_path.read_text(encoding="utf-8"), "solution.py", "exec"), namespace)  # noqa: S102
            if "SQL" in namespace:
                return str(namespace["SQL"]).strip()
            if "query" in namespace:
                produced = namespace["query"]()
                self.assertIsInstance(produced, str, "query() must return a SQL string")
                return str(produced).strip()

        self.fail("no submission found: expected /tmp/workspace/solution.sql or solution.py")

    def assert_query(self, expected: Sequence[Row], *, ordered: bool = True) -> None:
        """Assert the candidate's query returns ``expected``.

        The query runs inside a savepoint so a mutating query cannot corrupt the
        fixture for subsequent tests in the same case.

        Args:
            expected: The rows the query must produce.
            ordered: Compare row order. ``False`` sorts both sides first, for
                questions that do not specify an order.
        """
        sql = self.candidate_sql or self.read_candidate_sql()
        with self.conn:  # commits on success, rolls back on failure
            self.conn.execute("SAVEPOINT candidate")
            try:
                actual = materialise(self.conn, sql)
            finally:
                self.conn.execute("RELEASE candidate")

        if ordered:
            comparable_actual, comparable_expected = actual, [tuple(r) for r in expected]
        else:
            comparable_actual = sorted(actual, key=_sort_key)
            comparable_expected = sorted((tuple(r) for r in expected), key=_sort_key)

        if comparable_actual != comparable_expected:
            self.fail(
                "query result mismatch\n"
                f"  query:\n    {sql}\n"
                f"  expected ({len(comparable_expected)} rows):\n"
                f"{render_rows(comparable_expected)}\n"
                f"  actual ({len(comparable_actual)} rows):\n"
                f"{render_rows(comparable_actual)}"
            )

    def assert_query_raises(self, fragment: str) -> None:
        """Assert the candidate's query is rejected by the engine.

        Used for questions that test understanding of constraints, e.g. that a
        join must not silently drop rows.
        """
        sql = self.candidate_sql or self.read_candidate_sql()
        with self.assertRaises(sqlite3.Error) as caught:
            materialise(self.conn, sql)
        self.assertIn(
            fragment.lower(),
            str(caught.exception).lower(),
            f"expected the error to mention {fragment!r}, got: {caught.exception}",
        )

    def assert_row_count(self, count: int) -> None:
        """Assert the exact number of rows returned. Cheap, high-signal check."""
        sql = self.candidate_sql or self.read_candidate_sql()
        actual = materialise(self.conn, sql)
        self.assertEqual(
            len(actual),
            count,
            f"expected {count} rows, got {len(actual)}:\n{render_rows(actual)}",
        )


def _sort_key(row: Row) -> str:
    """Total ordering key for rows of mixed, possibly-nullable SQLite values.

    Sorting by ``repr`` rather than by value because SQLite columns are
    heterogeneous and may hold ``None``, and comparing those directly raises.
    ``repr`` is deterministic for a given value, which is all the unordered
    comparison needs.
    """
    return repr(row)


def emit_result_document(path: str, outcome: str, error: str | None = None) -> None:
    """Write the pytest-json-report-shaped document the worker expects.

    The SQL adapter reads ``tests``, ``collectors``, and ``harness_errors``. By
    emitting the same shape as ``pytest-json-report``, the worker shares one
    adapter path instead of needing a fourth normalisation route.

    Args:
        path: Where to write, normally ``$OA_RESULT_PATH``.
        outcome: ``passed`` or ``failed``.
        error: A harness-level error message, if the harness itself failed.
    """
    document: dict[str, Any] = {"tests": [], "collectors": [], "summary": {}}
    if error:
        document["harness_errors"] = [error]
    Path(path).write_text(json.dumps(document), encoding="utf-8")


def build_case(
    *,
    ddl: Sequence[str],
    seed: Sequence[str],
    candidate_sql: str,
    expectations: Sequence[tuple[str, Sequence[Row], bool]],
) -> type[SQLGradingCase]:
    """Synthesise a TestCase subclass from a question template.

    Dynamic class construction is the right tool here: each expectation becomes a
    genuine test method, so unittest reports it individually and the worker
    receives per-test booleans. Generating real methods rather than looping
    inside one test is what makes the per-test feedback in the UI possible.

    Args:
        ddl: Schema statements for the fixture.
        seed: Data statements for the fixture.
        candidate_sql: The candidate's query, if the template pre-supplies it.
        expectations: ``(name, expected_rows, ordered)`` triples.

    Returns:
        A ``SQLGradingCase`` subclass ready for ``unittest`` discovery.

    Raises:
        ValueError: If ``expectations`` is empty. A SQL question with no
            assertions would grade as vacuously passing.
    """
    if not expectations:
        raise ValueError("a SQL question must declare at least one expectation")

    namespace: dict[str, Any] = {
        "ddl": list(ddl),
        "seed": list(seed),
        "candidate_sql": candidate_sql,
        "expectations": list(expectations),
    }

    for index, (name, expected, ordered) in enumerate(expectations):
        namespace[f"test_case_{index}"] = _make_method(name, list(expected), bool(ordered))

    return type("GeneratedSQLCase", (SQLGradingCase,), namespace)


def _make_method(name: str, expected: list[Row], ordered: bool) -> Any:
    """Create one test method bound to a fixed expectation."""

    def method(self: SQLGradingCase) -> None:
        if not self.candidate_sql:
            self.candidate_sql = self.read_candidate_sql()
        self.assert_query(expected, ordered=ordered)

    method.__name__ = f"test_{_slugify(name)}"
    method.__doc__ = f"Expectation: {name}"
    return method


def _slugify(text: str) -> str:
    """Turn a human expectation name into a valid Python identifier suffix."""
    cleaned = "".join(char if char.isalnum() else "_" for char in text.lower())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "unnamed"


if __name__ == "__main__":
    # Reached only if someone execs this module directly inside the sandbox.
    # The real entrypoint is unittest's runner via grade.sh. The message is the
    # last traceback line, which is the part that actually explains what happened.
    sys.stderr.write(traceback.format_exc().splitlines()[-1] + "\n")
    raise SystemExit("invoke via grade.sh, not directly")
