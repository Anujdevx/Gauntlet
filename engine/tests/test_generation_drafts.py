"""Static checks on LLM drafts: every rule must reject what it claims to reject."""

from __future__ import annotations

import pytest

from oa_engine.generation import (
    DraftError,
    PythonDraft,
    SqlDraft,
    build_python_question,
    build_sql_question,
)

TESTS = """
import pytest
from solution import longest

@pytest.mark.parametrize("xs,n", [([], 0), ([1], 1), ([1, 1], 2)])
def test_basic(xs, n):
    assert longest(xs) == n

def test_a():
    assert longest([1, 2]) == 1

def test_b():
    assert longest([2, 2, 2]) == 3

def test_c():
    assert longest([1, 1, 2, 2, 2]) == 3
"""
REF = "def longest(xs):\n    best = cur = 0\n    prev = object()\n    for x in xs:\n        cur = cur + 1 if x == prev else 1\n        best = max(best, cur)\n        prev = x\n    return best\n"  # noqa: E501
START = "def longest(xs):\n    raise NotImplementedError\n"
BUGGY = [
    "def longest(xs):\n    return len(xs)\n",
    "def longest(xs):\n    return 1 if xs else 0\n",
    "def longest(xs):\n    return len(set(xs))\n",
]


def py_draft(**over: object) -> PythonDraft:
    base: dict[str, object] = {
        "title": "Longest run of equal values",
        "prompt": "Write `longest(xs)` returning the length of the longest run of equal "
        "adjacent values. The empty list returns 0. " * 2,
        "entry_point": "longest",
        "starter_code": START,
        "reference_code": REF,
        "test_code": TESTS,
        "buggy_solutions": BUGGY,
    }
    base.update(over)
    return PythonDraft.model_validate(base)


def test_python_happy_path() -> None:
    q = build_python_question(py_draft(), difficulty="easy")
    assert q.language == "python"
    assert set(q.hidden_tests) == {"test_solution.py"}
    assert len(q.buggy_files) == 3
    assert q.solution_path == "solution.py"


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"test_code": "import os\n" + TESTS}, "forbidden module 'os'"),
        ({"test_code": TESTS + "\nimport time\n"}, "forbidden module 'time'"),
        ({"test_code": TESTS.replace("def test_a", "@pytest.mark.skip\ndef test_a")}, "skip"),
        (
            {"test_code": "from solution import longest\ndef test_one():\n    assert 1\n"},
            "test functions",
        ),
        ({"test_code": "def test_a():\n    pass\n" * 1}, "import from the 'solution'"),
        ({"starter_code": "def other(xs):\n    pass\n"}, "must define 'longest'"),
        ({"starter_code": REF}, "identical to reference_code"),
        ({"buggy_solutions": [BUGGY[0], BUGGY[1], "def nope(:\n"]}, "syntax error"),
        ({"buggy_solutions": [BUGGY[0], BUGGY[1], "def zzz(xs):\n    return 1\n"]}, "must define"),
        ({"buggy_solutions": [BUGGY[0], BUGGY[0], BUGGY[1]]}, "duplicates"),
        ({"buggy_solutions": [REF, BUGGY[0], BUGGY[1]]}, "identical to reference_code"),
        ({"reference_code": REF + "\nopen('/etc/passwd')\n"}, "forbidden builtin 'open'"),
    ],
)
def test_python_rejections(override: dict[str, object], fragment: str) -> None:
    with pytest.raises(DraftError) as caught:
        build_python_question(py_draft(**override), difficulty="easy")
    assert fragment in " ".join(caught.value.problems)


def test_python_reports_all_problems_at_once() -> None:
    with pytest.raises(DraftError) as caught:
        build_python_question(
            py_draft(test_code="import os\nimport random\n", starter_code=REF), difficulty="easy"
        )
    assert len(caught.value.problems) >= 3


# ---------------------------------------------------------------- SQL

DDL = [
    "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total INTEGER NOT NULL)",
]
REF_SQL = (
    "SELECT c.name AS customer_name, SUM(o.total) AS spend FROM customers c "
    "JOIN orders o ON o.customer_id = c.id GROUP BY c.id ORDER BY spend DESC, c.id"
)


def sql_draft(**over: object) -> SqlDraft:
    base: dict[str, object] = {
        "title": "Customer spend ranking",
        "prompt": "Return `customer_name` and `spend` per customer, highest spend first. " * 3,
        "ddl": DDL,
        "scenarios": [
            {
                "name": "two customers with orders",
                "ordered": True,
                "seed": [
                    "INSERT INTO customers VALUES (1, 'ada'), (2, 'grace')",
                    "INSERT INTO orders VALUES (1, 1, 10), (2, 2, 50), (3, 1, 5)",
                ],
            },
            {
                "name": "a customer without orders",
                "ordered": True,
                "seed": [
                    "INSERT INTO customers VALUES (1, 'ada'), (2, 'linus')",
                    "INSERT INTO orders VALUES (1, 1, 7)",
                ],
            },
            {
                "name": "no orders at all",
                "ordered": True,
                "seed": ["INSERT INTO customers VALUES (1, 'ada')"],
            },
        ],
        "reference_sql": REF_SQL,
        "buggy_sqls": [
            "SELECT c.name, o.total FROM customers c JOIN orders o ON o.customer_id = c.id",
            "SELECT c.name, COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id",  # noqa: E501
            "SELECT name, 0 FROM customers",
        ],
    }
    base.update(over)
    return SqlDraft.model_validate(base)


def test_sql_expected_rows_are_computed_not_written() -> None:
    q = build_sql_question(sql_draft(), difficulty="easy")
    assert q.meta["expected_row_counts"] == [2, 1, 0]
    source = q.hidden_tests["test_query.py"]
    assert "('grace', 50)" in source and "('ada', 15)" in source


def test_sql_rendered_tests_are_valid_python_using_the_harness_api() -> None:
    q = build_sql_question(sql_draft(), difficulty="easy")
    calls: list[dict[str, object]] = []

    def build_case(**kwargs: object) -> type:
        calls.append(kwargs)
        # Like the real harness: one method per expectation, named test_case_<i>.
        return type("Case", (), {"test_case_0": lambda self: None})

    namespace: dict[str, object] = {}
    source = q.hidden_tests["test_query.py"].replace("from harness import build_case", "")
    exec(compile(source, "test_query.py", "exec"), {"build_case": build_case}, namespace)  # noqa: S102
    assert len(calls) == 3
    assert {c["candidate_sql"] for c in calls} == {""}
    assert set(namespace) >= {"Scenario0", "Scenario1", "Scenario2"}
    # Methods are renamed to the scenario's own name so results are readable.
    assert hasattr(namespace["Scenario1"], "test_a_customer_without_orders")
    assert not hasattr(namespace["Scenario1"], "test_case_0")


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"reference_sql": "DELETE FROM orders"}, "one SELECT"),
        ({"reference_sql": "SELECT 1; SELECT 2"}, "one SELECT"),
        ({"ddl": ["DROP TABLE orders"]}, "single CREATE"),
        ({"buggy_sqls": ["SELECT 1", "SELECT 1", "SELECT 2"]}, "duplicates"),
        ({"reference_sql": "SELECT nope FROM customers"}, "reference_sql failed"),
        (
            {"reference_sql": "SELECT AVG(total) FROM orders"},
            "raw float",
        ),
    ],
)
def test_sql_rejections(override: dict[str, object], fragment: str) -> None:
    draft = sql_draft(**override)
    # AVG case needs fractional data to produce a float.
    if "AVG" in str(override.get("reference_sql")):
        draft.scenarios[0].seed[1] = "INSERT INTO orders VALUES (1, 1, 10), (2, 2, 5), (3, 1, 7)"
    with pytest.raises(DraftError) as caught:
        build_sql_question(draft, difficulty="easy")
    assert fragment in " ".join(caught.value.problems)


def test_sql_rejects_seed_that_is_not_an_insert() -> None:
    draft = sql_draft()
    draft.scenarios[0].seed.append("DELETE FROM customers")
    with pytest.raises(DraftError, match="single INSERT"):
        build_sql_question(draft, difficulty="easy")


def test_sql_rejects_when_every_scenario_is_empty() -> None:
    scenarios = [
        {"name": f"empty {i}", "ordered": False, "seed": ["INSERT INTO customers VALUES (1, 'x')"]}
        for i in range(3)
    ]
    with pytest.raises(DraftError, match="zero rows"):
        build_sql_question(sql_draft(scenarios=scenarios), difficulty="easy")


def test_sql_rejects_identical_scenario_results() -> None:
    seed = [
        "INSERT INTO customers VALUES (1, 'ada')",
        "INSERT INTO orders VALUES (1, 1, 5)",
    ]
    scenarios = [{"name": f"same {i}", "ordered": False, "seed": seed} for i in range(3)]
    with pytest.raises(DraftError, match="same expected result"):
        build_sql_question(sql_draft(scenarios=scenarios), difficulty="easy")


def test_sql_starter_fails_even_scenarios_that_expect_no_rows() -> None:
    q = build_sql_question(sql_draft(), difficulty="easy")
    starter = q.starter_files["solution.sql"]
    assert "SELECT 'replace me'" in starter  # returns a row, so it never equals []


@pytest.mark.parametrize(
    "ramble",
    [
        "Return the top values. Let's specify the exact tie-break convention here.",
        "Sort by value, or simply ensure a stable order that is deterministic overall.",
        "Return the count. Wait, actually, return the maximum instead.",
        "Ties resolve by arrival (or any consistent stable order) for determinism.",
    ],
)
def test_prompts_that_deliberate_aloud_are_rejected(ramble: str) -> None:
    clean = py_draft().prompt
    with pytest.raises(DraftError) as caught:
        build_python_question(py_draft(prompt=clean + " " + ramble), difficulty="easy")
    assert "visible deliberation" in " ".join(caught.value.problems)


def test_a_clean_prompt_is_not_flagged() -> None:
    build_python_question(py_draft(), difficulty="easy")  # does not raise
