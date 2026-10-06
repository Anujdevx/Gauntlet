"""Regression: output at the envelope's byte cap must be representable."""

from __future__ import annotations

from oa_engine.execution import ExecutionResult, ExecutionStatus
from oa_engine.platform.config import DEFAULT_ENVELOPE


def test_stream_at_the_envelope_cap_does_not_crash_result_construction() -> None:
    cap = DEFAULT_ENVELOPE.max_stream_bytes
    result = ExecutionResult(
        submission_id="s",
        status=ExecutionStatus.FAILED,
        stdout="x" * cap,
        stderr="y" * cap,
        stdout_truncated=True,
        stderr_truncated=True,
    )
    assert len(result.stdout) == cap


def test_java_is_a_first_class_language() -> None:
    from oa_engine.platform.config import Language, Settings

    assert Language("java") is Language.JAVA
    assert Settings().image_for(Language.JAVA) == "oa/sandbox-java:1.0.0"
