"""Execution contracts shared between the sandbox workers and the control plane."""

from __future__ import annotations

from oa_engine.execution.result import (
    ExecutionResult,
    ExecutionStatus,
    ScoreBreakdown,
    TestCaseResult,
)

__all__ = ["ExecutionResult", "ExecutionStatus", "ScoreBreakdown", "TestCaseResult"]
