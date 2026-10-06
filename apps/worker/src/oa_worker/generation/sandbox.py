"""The one door from the generation pipeline into the sandbox.

A narrow protocol so the gate logic is testable without Docker, and so the pipeline
can only ever do one thing with the sandbox: run files against hidden tests.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from oa_engine.execution import ExecutionResult
from oa_engine.execution.payload import SubmissionPayload
from oa_engine.platform.config import DEFAULT_ENVELOPE, ExecutionLimits, Language, Settings
from oa_worker.sandbox.orchestrator import OrchestratorError, SandboxOrchestrator

__all__ = ["DockerSandbox", "Sandbox", "SandboxUnavailableError"]


class SandboxUnavailableError(RuntimeError):
    """The sandbox itself failed (Docker, image, runtime). Not the question's fault."""


class Sandbox(Protocol):
    def run(
        self,
        *,
        language: str,
        files: dict[str, str],
        hidden_tests: dict[str, str],
        run_command: tuple[str, ...],
    ) -> ExecutionResult: ...


class DockerSandbox:
    """Runs payloads through the production orchestrator, full security envelope."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._orchestrator = SandboxOrchestrator(settings=settings)

    def run(
        self,
        *,
        language: str,
        files: dict[str, str],
        hidden_tests: dict[str, str],
        run_command: tuple[str, ...],
    ) -> ExecutionResult:
        submission_id = str(uuid.uuid4())
        payload = SubmissionPayload(
            submission_id=submission_id,
            language=language,
            files=files,
            hidden_tests=hidden_tests,
            run_command=run_command,
        )
        limits = ExecutionLimits.build(
            Language(language), self._settings, DEFAULT_ENVELOPE, submission_id
        )
        try:
            return self._orchestrator.run(payload=payload, limits=limits)
        except OrchestratorError as exc:
            raise SandboxUnavailableError(str(exc)) from exc
