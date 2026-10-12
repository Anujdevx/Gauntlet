"""The unified execution result contract.

Every language's test runner normalises into exactly this shape, and the API
serves nothing else. That uniformity is what lets the skill engine grade across
languages without special cases, and what lets the frontend render one component
for Vitest, unittest, and pytest output.

Two rules keep this schema honest:

1. **No grading happens in this module.** A result describes what the hidden
   tests reported; it never infers a verdict from strings, exit codes, or log
   patterns. ``status`` is derived from ``tests`` by :meth:`TestCaseResult.
   derive_status`, which only ever looks at per-test booleans.
2. **Infrastructure failures are not candidate failures.** ``INFRA_ERROR`` and
   ``RESOURCE_EXCEEDED`` are distinct terminal states precisely so the mastery
   model can decline to update. Folding them into ``FAILED`` would teach the
   BKT model that the candidate is weak because our Docker daemon hiccuped.
"""

from __future__ import annotations

import enum
import math
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

__all__ = [
    "ExecutionResult",
    "ExecutionStatus",
    "ScoreBreakdown",
    "TestCaseResult",
]


#: One character per byte at most, so a stream capped at ``max_stream_bytes`` always fits.
STREAM_MAX_CHARS = 256 * 1024


class ExecutionStatus(str, enum.Enum):
    """Terminal state of a sandboxed run."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"
    RESOURCE_EXCEEDED = "resource_exceeded"
    INFRA_ERROR = "infra_error"

    @property
    def counts_against_candidate(self) -> bool:
        """Whether this outcome may be fed to the skill model as evidence.

        Only outcomes that genuinely reflect the candidate's code are
        admissible. A timeout is admissible -- the candidate wrote code that
        does not terminate under the stated limit, which is a real skill signal.
        Resource exhaustion is admissible for the same reason. Infrastructure
        errors are not, and must be retried or discarded.
        """
        return self in {
            ExecutionStatus.PASSED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.RESOURCE_EXCEEDED,
        }


class TestCaseResult(BaseModel):
    """One hidden test's outcome.

    Field names are chosen so the JSON is usable by the Vitest, unittest, and
    pytest adapters without a per-language branch in the frontend.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        min_length=1,
        max_length=256,
        description="Stable test identifier, e.g. 'sql.joins.inner::returns_matching_rows'.",
    )
    name: str = Field(min_length=1, max_length=512, description="Human-readable test name.")
    passed: bool = Field(description="Binary verdict from the runner. Never inferred.")
    duration_ms: float = Field(
        default=0.0, ge=0.0, description="Wall time attributed to this test."
    )
    message: str | None = Field(
        default=None,
        max_length=8000,
        description="Assertion diff or failure message, already truncated by the producer.",
    )
    stdout: str | None = Field(
        default=None, max_length=8000, description="Per-test stdout, if captured."
    )
    stderr: str | None = Field(
        default=None, max_length=8000, description="Per-test stderr, if captured."
    )
    stack_trace: str | None = Field(
        default=None, max_length=16000, description="Trimmed traceback."
    )
    skipped: bool = False
    param_ids: list[str] = Field(
        default_factory=list,
        max_length=64,
        description="Parametrisation keys for table-driven tests.",
    )

    @field_validator("message", "stdout", "stderr", "stack_trace", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        """Normalise empty strings to ``None`` so absent means absent."""
        return None if v == "" else v


class ScoreBreakdown(BaseModel):
    """Deterministic score derived only from hidden-test outcomes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(default=0, ge=0)
    total: int = Field(ge=0)
    #: Fraction of *runnable* tests passed, in [0, 1]. Skipped tests are excluded
    #: from the denominator so a runner that skips everything cannot score 1.0.
    ratio: float = Field(ge=0.0, le=1.0)
    #: Human-facing 0-100 score. Rounded to two decimals at the boundary so the
    #: stored value is exactly what the UI will render.
    percentage: float = Field(ge=0.0, le=100.0)

    @classmethod
    def from_tests(cls, tests: list[TestCaseResult]) -> Self:
        """Aggregate per-test verdicts into a score.

        A result with zero runnable tests scores 0.0 rather than raising:
        a language-level import error legitimately produces "no tests ran", and
        that is a candidate failure, not an orchestrator bug.
        """
        runnable = [t for t in tests if not t.skipped]
        passed = sum(1 for t in runnable if t.passed)
        failed = len(runnable) - passed
        skipped = sum(1 for t in tests if t.skipped)
        ratio = (passed / len(runnable)) if runnable else 0.0
        return cls(
            passed=passed,
            failed=failed,
            skipped=skipped,
            total=len(tests),
            ratio=ratio,
            percentage=round(ratio * 100, 2),
        )


class ExecutionResult(BaseModel):
    """Unified result envelope returned by every sandboxed run.

    This is the single object persisted to ``submissions`` and streamed to the
    browser. Producers are the per-language adapters in ``oa_worker.parsers``;
    consumers are the API and the skill engine.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    submission_id: str = Field(min_length=1, max_length=128)
    question_id: str | None = Field(
        default=None,
        max_length=128,
        description="Which generated question was graded, for template-level analytics.",
    )
    status: ExecutionStatus
    tests: tuple[TestCaseResult, ...] = Field(
        default=(),
        max_length=2000,
        description="Per-hidden-test outcomes, in runner-reported order.",
    )
    #: Must be >= the envelope's ``max_stream_bytes`` (256 KiB). A character limit below
    #: the byte cap made any stream between the two sizes crash result construction
    #: instead of being reported as truncated.
    stdout: str = Field(default="", max_length=STREAM_MAX_CHARS)
    stderr: str = Field(default="", max_length=STREAM_MAX_CHARS)
    runtime_ms: int = Field(default=0, ge=0, description="Sandboxed execution time only.")
    #: Truncation flags so the UI can warn the candidate rather than silently
    #: showing partial output as if it were complete.
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    #: The grader's own console output (pytest's failure report, Vitest's diff...). It
    #: contains the hidden tests' source lines, inputs and expected values, so it is
    #: ``exclude=True``: it is never dumped, persisted, streamed or returned by the API.
    #: Only trusted in-process callers (the question-generation gates) can read it.
    grader_output: str = Field(default="", max_length=STREAM_MAX_CHARS, exclude=True)
    score: ScoreBreakdown = Field(default_factory=lambda: ScoreBreakdown.from_tests([]))
    exit_code: int | None = None
    language: str | None = Field(default=None, max_length=32)
    image_digest: str | None = Field(
        default=None,
        max_length=128,
        description="Digest of the image that produced this result. Makes grading reproducible.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms_total: int | None = Field(
        default=None,
        ge=0,
        description="Enqueue-to-terminal wall clock, including queue wait and pool acquisition.",
    )

    @field_validator("stdout_truncated", "stderr_truncated", mode="after")
    @classmethod
    def _truncation_implies_content(cls, v: bool, info: Any) -> bool:
        """A stream can be truncated only if it carries content."""
        field_name = "stdout" if info.field_name == "stdout_truncated" else "stderr"
        if v and not info.data.get(field_name):
            raise ValueError(f"{info.field_name} cannot be true while {field_name} is empty")
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def counts_against_candidate(self) -> bool:
        """Whether this result is admissible evidence for mastery updates."""
        return self.status.counts_against_candidate

    def all_passed(self) -> bool:
        """True when every runnable hidden test passed."""
        return self.status is ExecutionStatus.PASSED

    @classmethod
    def from_tests(
        cls,
        *,
        submission_id: str,
        tests: list[TestCaseResult],
        status: ExecutionStatus | None = None,
        **kwargs: Any,
    ) -> Self:
        """Build a result, deriving :attr:`status` from ``tests`` when omitted.

        Derivation is intentionally narrow: ``PASSED`` iff at least one test ran
        and all of them passed. Anything else with no per-test data is ``ERROR``,
        because "we could not tell" must never be laundered into a fail-closed
        verdict that penalises the candidate.
        """
        score = ScoreBreakdown.from_tests(tests)
        runnable = [t for t in tests if not t.skipped]
        if status is None:
            if runnable and all(t.passed for t in runnable):
                status = ExecutionStatus.PASSED
            elif runnable:
                status = ExecutionStatus.FAILED
            else:
                status = ExecutionStatus.ERROR
        return cls(
            submission_id=submission_id,
            status=status,
            tests=tuple(tests),
            score=score,
            **kwargs,
        )

    def with_infrastructure_failure(
        self,
        detail: str,
        *,
        submission_id: str,
        question_id: str | None = None,
    ) -> Self:
        """Return a replacement marking this run as an orchestrator failure.

        Used when grading could not complete for platform reasons. Deliberately
        drops the partial test list: half-parsed output must not reach the UI
        as if it were a verdict.
        """
        truncated_detail = detail[:2000]
        return self.__class__(
            submission_id=submission_id,
            question_id=question_id,
            status=ExecutionStatus.INFRA_ERROR,
            tests=(),
            stdout="",
            stderr=truncated_detail,
            score=ScoreBreakdown.from_tests([]),
        )

    def to_stream_event(self) -> dict[str, Any]:
        """Serialise for the WebSocket channel consumed by the IDE terminal."""
        payload = self.model_dump(mode="json")
        payload["event"] = "result"
        return payload

    def duration_ms_sanitised(self) -> int:
        """Runtime in ms, guarding against clock skew producing negatives."""
        return int(max(0, self.runtime_ms)) if not math.isnan(self.runtime_ms) else 0
