"""Fakes for the sandbox and the LLM so gate logic is tested without Docker or a key."""

from __future__ import annotations

from typing import Any

import pytest

from oa_engine.execution import ExecutionResult, ExecutionStatus, TestCaseResult
from oa_engine.generation import PythonDraft, Question, build_python_question
from oa_engine.generation.prompts import BlindSolution, RelevanceVerdict
from oa_engine.llm import LLMError, LLMOutputError

TOTAL = 6

REF = "def longest(xs):\n    return REF\n"
START = "def longest(xs):\n    raise NotImplementedError\n"
BUGGY = [f"def longest(xs):\n    return {i}\n" for i in range(3)]
TESTS = (
    "import pytest\nfrom solution import longest\n\n"
    "@pytest.mark.parametrize('x', [1, 2, 3])\ndef test_a(x):\n    assert longest([x])\n\n"
    "def test_b():\n    assert longest([])\n\ndef test_c():\n    assert longest([1])\n\n"
    "def test_d():\n    assert longest([1, 1])\n"
)


def make_result(
    passed: int,
    total: int = TOTAL,
    *,
    status: ExecutionStatus | None = None,
    stderr: str = "",
    skipped: int = 0,
    ids: list[str] | None = None,
) -> ExecutionResult:
    tests = [
        TestCaseResult(
            id=(ids[i] if ids else f"t{i}"),
            name=f"test_{i}",
            passed=i < passed,
            message=None if i < passed else "assert 0 == 1",
            skipped=i >= total - skipped,
        )
        for i in range(total)
    ]
    return ExecutionResult.from_tests(
        submission_id="fake", tests=tests, status=status, stderr=stderr
    )


class FakeSandbox:
    """Maps the submitted solution text to a canned outcome; counts every run."""

    def __init__(self) -> None:
        self.outcomes: dict[str, ExecutionResult] = {}
        self.runs: list[str] = []
        self.default = make_result(0)

    def when(self, solution: str, result: ExecutionResult) -> None:
        self.outcomes[solution] = result

    def run(
        self,
        *,
        language: str,
        files: dict[str, str],
        hidden_tests: dict[str, str],
        run_command: tuple[str, ...],
    ) -> ExecutionResult:
        key = next(iter(files.values()))
        self.runs.append(key)
        return self.outcomes.get(key, self.default)


class FakeLLM:
    """Returns queued drafts / blind solutions, or raises queued errors."""

    model = "fake-model"

    def __init__(
        self,
        drafts: list[Any],
        solutions: list[str] | None = None,
        verdicts: list[Any] | None = None,
    ) -> None:
        self.drafts = list(drafts)
        self.solutions = list(solutions or [])
        self.verdicts = list(verdicts or [])
        self.prompts: list[str] = []

    def generate(
        self, *, system: str, prompt: str, schema: type[Any], temperature: float = 0.7
    ) -> Any:
        self.prompts.append(prompt)
        if schema is RelevanceVerdict:
            if self.verdicts:
                item = self.verdicts.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return RelevanceVerdict(
                primary_technique="the target", assesses_target_skill=True, reason=""
            )
        if schema is BlindSolution:
            return BlindSolution(solution=self.solutions.pop(0))
        item = self.drafts.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def draft(**over: Any) -> PythonDraft:
    base: dict[str, Any] = {
        "title": "Longest run of equal values",
        "prompt": "Write `longest(xs)` returning the length of the longest run of equal "
        "adjacent values; the empty list returns 0. " * 2,
        "entry_point": "longest",
        "starter_code": START,
        "reference_code": REF,
        "test_code": TESTS,
        "buggy_solutions": BUGGY,
    }
    base.update(over)
    return PythonDraft.model_validate(base)


@pytest.fixture
def question() -> Question:
    return build_python_question(draft(), difficulty="easy")


@pytest.fixture
def sandbox() -> FakeSandbox:
    box = FakeSandbox()
    box.when(REF, make_result(TOTAL))
    box.when(START, make_result(0))
    for code in BUGGY:
        box.when(code, make_result(2))
    return box


__all__ = [
    "FakeLLM",
    "FakeSandbox",
    "LLMError",
    "LLMOutputError",
    "RelevanceVerdict",
    "draft",
    "make_result",
]
