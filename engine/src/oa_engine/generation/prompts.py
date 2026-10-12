"""Prompts for question authoring and for the blind-solver check.

The contracts below mirror the sandbox runners exactly (pytest for Python, the
``harness.build_case`` SQL harness). If a runner changes, change it here too; the
gates will otherwise reject every draft with a confusing error.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "SYSTEM",
    "BlindSolution",
    "RelevanceVerdict",
    "authoring_prompt",
    "blind_prompt",
    "relevance_prompt",
]

SYSTEM = (
    "You are a senior software engineer who writes rigorous technical-assessment "
    "questions with hidden automated tests. Your questions are practical, realistic "
    "engineering tasks, never abstract puzzles. You are meticulous: every test you "
    "write must pass for ANY correct solution, not just yours. You never write "
    "ambiguous prompts. Output only the requested JSON."
)

DIFFICULTY_GUIDE = {
    "easy": "One core idea, solvable in 5-10 minutes in under ~15 lines. Few edge cases.",
    "medium": "Two ideas combined, ~15-20 minutes, several edge cases a careful engineer notices.",
    "hard": "Subtle: an invariant, a tricky edge case or a complexity bound. ~30 minutes.",
}

_QUALITY_BAR = """\
QUALITY BAR (all are checked automatically; a draft that fails any is discarded)
- The prompt fully specifies behaviour. If the tests check it, the prompt says it:
  exact names, signatures, types, tie-breaking, empty inputs, error behaviour.
- Tests are deterministic and offline: no randomness, clocks, sleeps, files,
  network, environment access.
- Tests accept EVERY correct implementation. Never assert on implementation
  details, exception messages (unless the prompt dictates them), or call counts.
- Test names describe the scenario neutrally and never reveal the solution.
- A separate engineer will solve your question seeing ONLY the prompt and starter
  code. If they could reasonably return a different answer than your tests expect,
  the question is rejected.
- Every wrong solution you supply must fail at least one test, and each must be
  wrong in a DIFFERENT realistic way (off-by-one, missed edge case, mutates input,
  wrong tie-break, wrong complexity class on large input...).
- Do not copy a famous interview problem verbatim; ground it in a realistic scenario.
- Write the prompt in plain Markdown. NO LaTeX or $...$ math: write `0 <= N <= 100000`,
  `10^9` and `O(N log N)` as plain text or in backticks.
"""

_PYTHON_CONTRACT = """\
PYTHON CONTRACT
- The candidate edits one file, `solution.py`. Tests live in `test_solution.py`
  and import with `from solution import <entry_point>`.
- Use pytest. Use `@pytest.mark.parametrize` for tables of cases. Write at least 6
  distinct cases across at least 4 `test_` functions, including empty/minimal input
  and at least one adversarial edge case.
- At most ONE test may use a large input (<= 100000 elements) to enforce a
  complexity requirement stated in the prompt; use no timing calls.
- Allowed imports in tests: pytest, typing, collections, itertools, functools,
  heapq, bisect, math, re, dataclasses, asyncio, unittest.mock. Standard library
  only. Do NOT import: os, sys, time, datetime, random, threading, subprocess,
  pathlib, sqlite3 or anything networked. Also NO file access or dynamic code:
  never call open(), input(), eval(), exec() or __import__ in tests, the reference,
  or any wrong solution. Model I/O-ish tasks with in-memory data (strings, lists,
  dicts, io.StringIO) instead.
- `starter_code`: valid Python that defines `entry_point` with a full signature and
  docstring and then `raise NotImplementedError`. It must import and not run
  anything at import time.
- `reference_code` and every entry of `buggy_solutions` are complete `solution.py`
  files that ALL define `entry_point` at module level and import cleanly (a wrong
  solution must be wrong in behaviour, not broken to import).
"""

_DSA_GUIDELINES = """\
DSA GUIDELINES (an algorithms question, framed realistically)
- The core of the task must be the named technique; the scenario is a thin, realistic
  wrapper (logs, inventory, schedules, networks), not the point.
- Use ONLY plain types in signatures. Python: list, dict, tuple, str, int, bool. Java:
  int[], long, String, List<Integer>, List<List<Integer>>, Map<String, Integer>. For trees
  and graphs use arrays, adjacency lists/dicts or edge lists. NEVER require a custom
  node class, and never hand the solver a pre-built TreeNode or ListNode.
- Define the exact return convention. If several valid answers can exist (a path, a
  permutation, a set of pairs), the prompt MUST force a single answer (a tie-break rule, a
  required order, "lexicographically smallest") or require sorted output, so every correct
  solution returns the same value.
- State the required time complexity in the prompt. Include exactly ONE large-input
  test (at most 100000 elements) that a worse complexity class would not finish; an
  O(n^2) approach on 100000 elements must fail it.
- Cover: empty and single-element input, duplicates or ties, an all-equal input, and an
  already-sorted and a reverse-sorted input where they matter.
- Do not test inputs that need recursion deeper than ~900 frames unless the prompt says the
  solution must be iterative.
- Among your wrong solutions, include at most ONE that is correct but too slow (the
  brute-force version); the others must be wrong in behaviour.
"""

_JAVA_CONTRACT = """\
JAVA CONTRACT (Java 21, JUnit 5)
- The candidate edits one file, `Solution.java`: `public class Solution`, DEFAULT
  package (no `package` line). Tests live in `SolutionTest.java` (`class SolutionTest`)
  and call `Solution` directly. Prefer public static methods unless the task is
  naturally about an object (then a small class with a public constructor and methods).
- Tests use `org.junit.jupiter.api.Test` and static `Assertions.*`. Use
  `@ParameterizedTest` with `@CsvSource` / `@MethodSource` / `@ValueSource` (from
  `org.junit.jupiter.params`) for tables of cases. Write at least 6 distinct cases
  across at least 4 test methods, including empty/minimal input and at least one
  adversarial edge case. Name each test method after the scenario it checks, in
  lowerCamelCase, without revealing the technique.
- Allowed: the Java standard library (java.util.*, java.util.stream.*,
  java.util.function.*, java.math.*, java.time WITHOUT now()). Do NOT use: file or
  network I/O, threads/sleep, randomness, the clock, reflection, System.exit,
  environment or properties, @Disabled or Assumptions.
- `starter_code`: compiles, declares the exact public signatures with javadoc, and each
  body does `throw new UnsupportedOperationException("TODO");`.
- `reference_code` and every `buggy_solutions` entry are COMPLETE `Solution.java` files
  with the same public API that COMPILE cleanly (a wrong solution must be wrong in
  behaviour, never a compile error). Include needed imports.
- Use `assertEquals(expected, actual)` argument order; compare collections with
  `assertEquals`/`assertIterableEquals`; for floating point avoid exact comparison.
"""

_SQL_CONTRACT = """\
SQL CONTRACT (SQLite dialect)
- Provide `ddl` (CREATE TABLE statements) and 3-6 `scenarios`, each with its own
  `seed` INSERT statements. The same query runs against each scenario's data; ALL
  scenarios must have different data, and together they must cover: a typical case,
  ties, NULLs where relevant, an empty or no-match case, and a case that defeats
  hard-coded answers.
- You do NOT write expected rows. They are computed by running your `reference_sql`.
  So `reference_sql` must be exactly right.
- The prompt must list the table schemas, the EXACT output columns in order, and the
  row-ordering rule. Set `ordered` true only if the prompt defines a TOTAL order
  (include a unique tiebreak column in the ORDER BY); otherwise false.
- Never return raw floating-point averages or ratios; require ROUND(x, 2) in the
  prompt and use it in `reference_sql`.
- `reference_sql` and each `buggy_sqls` entry is ONE statement: SELECT, or
  WITH ... SELECT. No semicolons inside. No PRAGMA, no writes.
- Scenario `name`s describe the data neutrally (e.g. "customers with no orders"),
  never the technique (not "needs a LEFT JOIN").
"""


def authoring_prompt(
    *,
    language: str,
    node_id: str,
    node_label: str,
    node_description: str,
    difficulty: str,
    existing_titles: list[str],
    feedback: str | None,
) -> str:
    """Build the user prompt for one authoring attempt."""
    contract = {"python": _PYTHON_CONTRACT, "sql": _SQL_CONTRACT, "java": _JAVA_CONTRACT}[language]
    parts = [
        f"Write ONE {difficulty} {language.upper()} assessment question.",
        "",
        f"SKILL BEING TESTED: {node_id} ({node_label}): {node_description}",
        f"DIFFICULTY: {difficulty}. {DIFFICULTY_GUIDE[difficulty]}",
        "",
        contract,
        *([_DSA_GUIDELINES] if node_id.split(".", maxsplit=1)[0] == "dsa" else []),
        _QUALITY_BAR,
    ]
    if existing_titles:
        parts += [
            "ALREADY IN THE BANK (do not repeat these ideas or scenarios):",
            *[f"- {title}" for title in existing_titles[:30]],
            "",
        ]
    if feedback:
        parts += [
            "YOUR PREVIOUS ATTEMPT WAS REJECTED. Fix every point below, keep what was "
            "good, and return a complete new question:",
            feedback,
            "",
        ]
    return "\n".join(parts)


class BlindSolution(BaseModel):
    """The blind solver's answer: one complete file."""

    solution: str = Field(description="The complete contents of the solution file.")


def blind_prompt(*, language: str, prompt: str, starter: str) -> str:
    """Prompt for the blind solver. It sees the task and starter, NEVER the tests."""
    target = {
        "python": "solution.py",
        "sql": "solution.sql (one SELECT statement)",
        "java": "Solution.java (public class Solution, default package)",
    }[language]
    return (
        f"Solve this task. Return the complete contents of {target}.\n"
        "Follow the task statement literally; where it is silent, choose the most "
        "natural behaviour.\n\n"
        f"TASK:\n{prompt}\n\n"
        f"STARTER FILE:\n{starter}\n"
    )


class RelevanceVerdict(BaseModel):
    """A second opinion on whether a question assesses the skill it is filed under."""

    primary_technique: str = Field(
        description="The single main technique or concept needed to solve the question."
    )
    assesses_target_skill: bool = Field(
        description="True only if that technique IS the target skill (or a core part of it)."
    )
    reason: str = Field(description="One or two sentences justifying the verdict.")


def relevance_prompt(
    *,
    node_id: str,
    node_label: str,
    node_description: str,
    title: str,
    prompt: str,
    reference: str,
) -> str:
    """Ask whether a question really tests the skill it is tagged with.

    Mastery tracking credits every outcome to the question's skill node, so a question
    that is secretly about something else (a "backtracking" question that is really a
    heap-based topological sort) would teach the skill model the wrong thing.
    """
    return (
        "You are auditing an assessment question for skill fidelity. Be strict.\n\n"
        f"TARGET SKILL: {node_id} ({node_label}): {node_description}\n\n"
        f"QUESTION TITLE: {title}\n\nQUESTION:\n{prompt}\n\n"
        f"REFERENCE SOLUTION:\n{reference}\n\n"
        "1. Name the single main technique a solver needs.\n"
        "2. Decide whether that technique is the target skill. If a different, more "
        "specific technique is the natural way to solve it (for example a heap, a "
        "topological sort, binary search, or plain iteration instead of the target), answer "
        "false."
    )
