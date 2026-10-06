"""What the LLM returns, and the static checks that turn it into a ``Question``.

Two rules keep LLM-written tests honest, and both live here rather than in a prompt:

1. **The model never writes expected values for SQL.** It supplies a schema, seed
   data per scenario, and a reference query. The expected rows are *computed* by
   running that query, so a confidently wrong hand-written answer cannot exist.
2. **Every file is checked before any sandbox time is spent.** A syntax error, a
   missing entry point, a network import or a skipped test is rejected here, with a
   message precise enough to feed straight back to the model.

``DraftError`` carries a list of problems, not the first one found: one retry that
fixes five issues beats five retries that fix one each, and free-tier quota is
the scarce resource.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "DraftError",
    "JavaDraft",
    "PythonDraft",
    "Question",
    "SqlDraft",
    "SqlScenario",
    "build_java_question",
    "build_python_question",
    "build_sql_question",
    "render_sql_tests",
]

#: Modules no generated file may import. Tests must be deterministic, offline and
#: self-contained; the sandbox also blocks these, but a question that needs them
#: can never be graded fairly, so it is rejected before it costs a container.
FORBIDDEN_MODULES = frozenset(
    {
        "socket",
        "requests",
        "urllib",
        "http",
        "httpx",
        "subprocess",
        "os",
        "shutil",
        "random",
        "secrets",
        "ctypes",
        "multiprocessing",
        "threading",
        "time",
        "datetime",
        "pathlib",
        "sys",
        "importlib",
        "pickle",
        "sqlite3",
    }
)
MIN_TEST_FUNCTIONS = 4
MIN_SQL_SCENARIOS = 3
#: A blank query returns zero rows, which would "pass" any scenario that expects none.
#: The placeholder returns one row, so it fails every scenario, empty ones included.
SQL_STARTER = "-- Replace the placeholder below with your query.\nSELECT 'replace me' AS todo;\n"
MIN_DISTINCT_RESULTS = 2


class DraftError(ValueError):
    """The draft violates a rule. ``problems`` is meant to be shown to the model."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# ---------------------------------------------------------------------------
# LLM response schemas
# ---------------------------------------------------------------------------


class PythonDraft(BaseModel):
    """A Python question: pytest tests over a ``solution.py`` module."""

    title: str = Field(min_length=5, max_length=120, description="Short, specific title.")
    prompt: str = Field(
        min_length=80,
        max_length=4000,
        description=(
            "Markdown task statement: scenario, exact function signature, input and "
            "output rules, edge cases, complexity requirement if the tests need one."
        ),
    )
    entry_point: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Name of the function or class the tests import from solution.py.",
    )
    starter_code: str = Field(
        description=(
            "solution.py given to the candidate: signature, docstring, then "
            "raise NotImplementedError."
        )
    )
    reference_code: str = Field(description="A correct, complete solution.py.")
    test_code: str = Field(
        description="A pytest file `test_solution.py` that does `from solution import ...`."
    )
    buggy_solutions: list[str] = Field(
        min_length=3,
        max_length=6,
        description=(
            "Complete solution.py files that each look plausible but are WRONG in a "
            "different realistic way (off-by-one, missed edge case, mutated input...)."
        ),
    )


class JavaDraft(BaseModel):
    """A Java question: JUnit 5 tests over ``Solution.java`` (default package)."""

    title: str = Field(min_length=5, max_length=120, description="Short, specific title.")
    prompt: str = Field(
        min_length=80,
        max_length=4000,
        description=(
            "Markdown task statement: scenario, exact method signatures in the "
            "`Solution` class, input and output rules, edge cases, complexity if tested."
        ),
    )
    starter_code: str = Field(
        description=(
            "Solution.java given to the candidate: `public class Solution` with the "
            "required signatures and javadoc, bodies throwing UnsupportedOperationException."
        )
    )
    reference_code: str = Field(description="A correct, complete Solution.java.")
    test_code: str = Field(
        description="A JUnit 5 file `SolutionTest.java` (class SolutionTest) testing `Solution`."
    )
    buggy_solutions: list[str] = Field(
        min_length=3,
        max_length=6,
        description=(
            "Complete Solution.java files that compile and look plausible but are WRONG "
            "in a different realistic way each."
        ),
    )


class SqlScenario(BaseModel):
    name: str = Field(
        min_length=3,
        max_length=80,
        description="Neutral description of what this data exercises. Never reveals the solution.",
    )
    seed: list[str] = Field(min_length=1, description="INSERT statements for this scenario.")
    ordered: bool = Field(
        description="True only if the prompt defines a total row order (include a unique tiebreak)."
    )


class SqlDraft(BaseModel):
    """A SQL question: a query graded against several seeded scenarios."""

    title: str = Field(min_length=5, max_length=120)
    prompt: str = Field(
        min_length=80,
        max_length=4000,
        description=(
            "Markdown task statement. Must give the schema, the EXACT output columns "
            "in order, and the row ordering rule (if any)."
        ),
    )
    ddl: list[str] = Field(min_length=1, description="CREATE TABLE statements (SQLite dialect).")
    scenarios: list[SqlScenario] = Field(min_length=3, max_length=6)
    reference_sql: str = Field(description="One correct SELECT (or WITH ... SELECT) statement.")
    buggy_sqls: list[str] = Field(
        min_length=3,
        max_length=6,
        description="Plausible but WRONG queries, each wrong in a different way.",
    )


# ---------------------------------------------------------------------------
# Normalised result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    """A language-neutral, ready-to-validate question."""

    language: str
    title: str
    prompt: str
    starter_files: dict[str, str]
    reference_files: dict[str, str]
    hidden_tests: dict[str, str]
    buggy_files: list[dict[str, str]]
    run_command: tuple[str, ...]
    #: Files the blind solver must produce, e.g. ``solution.py``.
    solution_path: str
    #: Extra detail for the report (scenario count, expected row counts...).
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def _check_python_source(label: str, source: str, problems: list[str]) -> ast.Module | None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        problems.append(f"{label}: syntax error on line {exc.lineno}: {exc.msg}")
        return None
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name.split(".")[0] in FORBIDDEN_MODULES:
                problems.append(
                    f"{label}: imports forbidden module '{name}' (offline, deterministic only)"
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            in {
                "open",
                "input",
                "eval",
                "exec",
                "__import__",
            }
        ):
            problems.append(f"{label}: calls forbidden builtin '{node.func.id}'")
    return tree


def _defines(tree: ast.Module, name: str) -> bool:
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
        for node in tree.body
    ) or any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        for node in tree.body
    )


def build_python_question(draft: PythonDraft, *, difficulty: str) -> Question:
    """Statically validate a Python draft and normalise it.

    Raises:
        DraftError: With every problem found.
    """
    problems: list[str] = []

    starter = _check_python_source("starter_code", draft.starter_code, problems)
    reference = _check_python_source("reference_code", draft.reference_code, problems)
    tests = _check_python_source("test_code", draft.test_code, problems)
    buggy_trees = [
        _check_python_source(f"buggy_solutions[{i}]", code, problems)
        for i, code in enumerate(draft.buggy_solutions)
    ]

    # A buggy solution that does not define the entry point would be "killed" by an
    # ImportError, not by a test of behaviour. That is a fake kill, so reject it.
    for label, tree in [("starter_code", starter), ("reference_code", reference)] + [
        (f"buggy_solutions[{i}]", tree) for i, tree in enumerate(buggy_trees)
    ]:
        if tree is not None and not _defines(tree, draft.entry_point):
            problems.append(f"{label}: must define '{draft.entry_point}' at module level")

    if tests is not None:
        test_functions = [
            node
            for node in ast.walk(tests)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        if len(test_functions) < MIN_TEST_FUNCTIONS:
            problems.append(
                f"test_code: need {MIN_TEST_FUNCTIONS}+ test functions, found {len(test_functions)}"
            )
        imports_solution = any(
            (isinstance(n, ast.ImportFrom) and n.module == "solution")
            or (isinstance(n, ast.Import) and any(a.name == "solution" for a in n.names))
            for n in ast.walk(tests)
        )
        if not imports_solution:
            problems.append("test_code: must import from the 'solution' module")
        if re.search(r"\b(skip|skipif|xfail)\b", draft.test_code):
            problems.append(
                "test_code: skip/skipif/xfail are not allowed (a skipped test is a free pass)"
            )

    if draft.starter_code.strip() == draft.reference_code.strip():
        problems.append("starter_code is identical to reference_code")
    if len({code.strip() for code in draft.buggy_solutions}) < len(draft.buggy_solutions):
        problems.append("buggy_solutions contains duplicates")
    if any(code.strip() == draft.reference_code.strip() for code in draft.buggy_solutions):
        problems.append("a buggy solution is identical to reference_code")

    if problems:
        raise DraftError(problems)

    return Question(
        language="python",
        title=draft.title.strip(),
        prompt=draft.prompt.strip(),
        starter_files={"solution.py": draft.starter_code},
        reference_files={"solution.py": draft.reference_code},
        hidden_tests={"test_solution.py": draft.test_code},
        buggy_files=[{"solution.py": code} for code in draft.buggy_solutions],
        run_command=("/bin/sh", "/runner/run-python.sh"),
        solution_path="solution.py",
        meta={"entry_point": draft.entry_point, "test_functions": len(test_functions)},
    )


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

#: Source fragments that make a Java test non-deterministic, non-hermetic or able to
#: escape the harness. Matched on text because Java has no stdlib parser to lean on;
#: compilation itself is checked by the sandbox gates with the real compiler.
FORBIDDEN_JAVA = (
    (r"\bjava\.io\.File", "file access"),
    (r"\bjava\.nio\.file", "file access"),
    (r"\bjava\.net\b", "network access"),
    (r"\bjavax?\.sql\b", "database access"),
    (r"\bProcessBuilder\b|\bRuntime\.getRuntime", "process control"),
    (r"\bSystem\.exit\b|\bRuntime\.halt\b", "exiting the JVM"),
    (r"\bThread\.sleep\b", "sleeping (slow and timing-dependent)"),
    (r"\bjava\.util\.Random\b|\bMath\.random\b|\bThreadLocalRandom\b", "randomness"),
    (r"\bSystem\.(currentTimeMillis|nanoTime)\b", "reading the clock"),
    (r"\b(LocalDate|LocalDateTime|Instant|ZonedDateTime)\.now\b", "reading the clock"),
    (r"\bSystem\.(getenv|getProperty|setProperty)\b", "environment access"),
    (r"\bjava\.lang\.reflect\b|\bClass\.forName\b|\bsetAccessible\b", "reflection"),
    (r"\bClassLoader\b|\bsun\.|\bUnsafe\b", "low-level JVM access"),
    (r"@Disabled\b|\bAssumptions\b|\bassumeTrue\b|\bassumeFalse\b", "skipped/aborted tests"),
)
_JAVA_SOLUTION_CLASS = re.compile(r"\bpublic\s+(final\s+)?class\s+Solution\b")
_JAVA_TEST_CLASS = re.compile(r"\bclass\s+SolutionTest\b")
_JAVA_TEST_METHOD = re.compile(r"@(Test|ParameterizedTest|RepeatedTest)\b")


def _check_java_source(label: str, source: str, problems: list[str]) -> None:
    if re.search(r"^\s*package\s", source, re.M):
        problems.append(f"{label}: must use the default package (no `package` statement)")
    for pattern, reason in FORBIDDEN_JAVA:
        if re.search(pattern, source):
            problems.append(f"{label}: forbidden ({reason}): matches /{pattern}/")


def build_java_question(draft: JavaDraft, *, difficulty: str) -> Question:
    """Statically validate a Java draft and normalise it.

    Syntax is *not* checked here: the sandbox gates compile every file with the real
    ``javac`` and feed the diagnostics back, which beats approximating a parser.

    Raises:
        DraftError: With every problem found.
    """
    problems: list[str] = []
    files = {
        "starter_code": draft.starter_code,
        "reference_code": draft.reference_code,
        "test_code": draft.test_code,
        **{f"buggy_solutions[{i}]": code for i, code in enumerate(draft.buggy_solutions)},
    }
    for label, source in files.items():
        _check_java_source(label, source, problems)
        if label != "test_code" and not _JAVA_SOLUTION_CLASS.search(source):
            problems.append(f"{label}: must declare `public class Solution`")

    if not _JAVA_TEST_CLASS.search(draft.test_code):
        problems.append("test_code: must declare `class SolutionTest`")
    if "org.junit.jupiter" not in draft.test_code:
        problems.append("test_code: must use JUnit 5 (org.junit.jupiter.api)")
    test_methods = len(_JAVA_TEST_METHOD.findall(draft.test_code))
    if test_methods < MIN_TEST_FUNCTIONS:
        problems.append(
            f"test_code: need {MIN_TEST_FUNCTIONS}+ @Test methods, found {test_methods}"
        )

    if draft.starter_code.strip() == draft.reference_code.strip():
        problems.append("starter_code is identical to reference_code")
    if len({code.strip() for code in draft.buggy_solutions}) < len(draft.buggy_solutions):
        problems.append("buggy_solutions contains duplicates")
    if any(code.strip() == draft.reference_code.strip() for code in draft.buggy_solutions):
        problems.append("a buggy solution is identical to reference_code")
    if problems:
        raise DraftError(problems)

    return Question(
        language="java",
        title=draft.title.strip(),
        prompt=draft.prompt.strip(),
        starter_files={"Solution.java": draft.starter_code},
        reference_files={"Solution.java": draft.reference_code},
        hidden_tests={"SolutionTest.java": draft.test_code},
        buggy_files=[{"Solution.java": code} for code in draft.buggy_solutions],
        run_command=("/bin/sh", "/runner/run-java.sh"),
        solution_path="Solution.java",
        meta={"test_methods": test_methods},
    )


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_DDL = re.compile(r"^\s*CREATE\s+(TABLE|INDEX|VIEW|UNIQUE\s+INDEX)\b", re.I)
_INSERT = re.compile(r"^\s*INSERT\s+(OR\s+\w+\s+)?INTO\b", re.I)
_SELECT = re.compile(r"^\s*(SELECT|WITH)\b", re.I)

#: sqlite3 authorizer actions a reference query may trigger.
_READ_ONLY_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}


def _single_statement(sql: str) -> bool:
    """True if ``sql`` is one statement (a trailing semicolon is fine)."""
    body = sql.strip().rstrip(";").strip()
    return ";" not in body and bool(body) and sqlite3.complete_statement(body + ";")


def _authorizer(action: int, *_: Any) -> int:
    return sqlite3.SQLITE_OK if action in _READ_ONLY_ACTIONS else sqlite3.SQLITE_DENY


def run_reference(ddl: list[str], seed: list[str], query: str) -> list[tuple[Any, ...]]:
    """Execute ``query`` read-only on a fresh in-memory database and return its rows."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for statement in [*ddl, *seed]:
            conn.execute(statement)
        conn.commit()
        conn.set_authorizer(_authorizer)
        return [tuple(row) for row in conn.execute(query.strip().rstrip(";")).fetchall()]
    finally:
        conn.close()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "case"


def render_sql_tests(
    ddl: list[str], scenarios: list[SqlScenario], expected: list[list[tuple[Any, ...]]]
) -> str:
    """Render the hidden test module. Expected rows come from code, never the model."""
    lines = [
        "from harness import build_case",
        "",
        f"DDL = {ddl!r}",
        "",
    ]
    for index, (scenario, rows) in enumerate(zip(scenarios, expected, strict=True)):
        lines += [
            f"Scenario{index} = build_case(",
            "    ddl=DDL,",
            f"    seed={scenario.seed!r},",
            "    candidate_sql='',",
            f"    expectations=[({scenario.name!r}, {rows!r}, {scenario.ordered!r})],",
            ")",
            # The harness names every method `test_case_<i>`; pytest reports the
            # attribute name, so rename it to the scenario's own name or every result
            # in the UI reads "test_case_0".
            f"Scenario{index}.test_{_slug(scenario.name)} = Scenario{index}.test_case_0",
            f"del Scenario{index}.test_case_0",
            "",
        ]
    return "\n".join(lines)


def build_sql_question(draft: SqlDraft, *, difficulty: str) -> Question:
    """Statically validate a SQL draft, compute expected rows, and normalise it.

    Raises:
        DraftError: With every problem found.
    """
    problems: list[str] = []

    for i, statement in enumerate(draft.ddl):
        if not _DDL.match(statement) or not _single_statement(statement):
            problems.append(f"ddl[{i}]: must be a single CREATE TABLE/INDEX/VIEW statement")
    names = [s.name.strip().lower() for s in draft.scenarios]
    if len(set(names)) != len(names):
        problems.append("scenario names must be unique")
    if len(draft.scenarios) < MIN_SQL_SCENARIOS:
        problems.append(f"need at least {MIN_SQL_SCENARIOS} scenarios")
    for si, scenario in enumerate(draft.scenarios):
        for i, statement in enumerate(scenario.seed):
            if not _INSERT.match(statement) or not _single_statement(statement):
                problems.append(f"scenarios[{si}].seed[{i}]: must be a single INSERT statement")
    for label, query in [("reference_sql", draft.reference_sql)] + [
        (f"buggy_sqls[{i}]", q) for i, q in enumerate(draft.buggy_sqls)
    ]:
        if not _SELECT.match(query) or not _single_statement(query):
            problems.append(f"{label}: must be one SELECT or WITH...SELECT statement")
    if len({q.strip() for q in draft.buggy_sqls}) < len(draft.buggy_sqls):
        problems.append("buggy_sqls contains duplicates")
    if problems:
        raise DraftError(problems)

    expected: list[list[tuple[Any, ...]]] = []
    for si, scenario in enumerate(draft.scenarios):
        try:
            rows = run_reference(draft.ddl, scenario.seed, draft.reference_sql)
        except sqlite3.Error as exc:
            problems.append(
                f"scenarios[{si}] '{scenario.name}': reference_sql failed on this data: {exc}"
            )
            continue
        for row in rows:
            for value in row:
                if isinstance(value, float) and round(value, 4) != value:
                    problems.append(
                        f"scenarios[{si}]: reference returns a raw float ({value!r}); "
                        "use ROUND(x, 2) so equivalent queries compare equal"
                    )
                    break
        expected.append(rows)
    if problems:
        raise DraftError(problems)

    if all(not rows for rows in expected):
        problems.append(
            "every scenario returns zero rows; the tests would accept an empty query result"
        )
    if len({repr(rows) for rows in expected}) < MIN_DISTINCT_RESULTS:
        problems.append(
            "all scenarios have the same expected result; use different data per scenario"
        )
    if problems:
        raise DraftError(problems)

    return Question(
        language="sql",
        title=draft.title.strip(),
        prompt=draft.prompt.strip(),
        starter_files={"solution.sql": SQL_STARTER},
        reference_files={"solution.sql": draft.reference_sql.strip() + "\n"},
        hidden_tests={"test_query.py": render_sql_tests(draft.ddl, draft.scenarios, expected)},
        buggy_files=[{"solution.sql": q.strip() + "\n"} for q in draft.buggy_sqls],
        run_command=("/bin/sh", "/runner/run-sql.sh"),
        solution_path="solution.sql",
        meta={
            "scenarios": len(draft.scenarios),
            "expected_row_counts": [len(rows) for rows in expected],
        },
    )
