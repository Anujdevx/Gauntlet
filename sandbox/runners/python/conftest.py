"""Python grading harness.

Invoked by :file:`sandbox/runners/python/grade.sh` as
``python -m pytest /grading --json-report ...``. This module exists for one
reason: to make the *whole collection phase* reportable, including the failures
that happen before any test can run.

Without it, a submission that fails to import produces a ``result.json`` with an
empty ``tests`` array. That is indistinguishable from "the grader crashed", and
the worker maps an empty array to ``ERROR`` — which would tell the candidate
"we could not determine your result" when in fact their code raised
``ModuleNotFoundError`` on line one. Recording collection failures as explicit
test cases is what keeps that distinction.

Determinism notes:

* Nothing here imports a network client. RAG and LLM questions bring their own
  mocks, so a candidate cannot make grading depend on a live endpoint.
* No wall-clock-dependent assertions. ``--timeout`` is enforced by the plugin, so
  a hung test is reported as a failure rather than stalling the whole run.
* Exit code is irrelevant to grading. This module does not choose it.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

#: Where the grader writes its side-channel diagnostics. The worker does not read
#: this; it exists for post-mortem debugging of a grading run.
DIAGNOSTIC_PATH = Path(os.environ.get("OA_DIAGNOSTIC_PATH", "/tmp/oa-grade-diagnostic.json"))


def _flatten_collect_error(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalise a collector failure into the same shape as a test failure.

    Args:
        entry: One entry from ``pytest-json-report``'s ``collectors`` array.

    Returns:
        A record with ``nodeid``, ``outcome``, ``duration``, and ``longrepr``
        keyed identically to a real test entry, so the worker's parser can treat
        collection errors and test failures uniformly.
    """
    longrepr = entry.get("longrepr")
    message: str | None = None
    if isinstance(longrepr, str):
        message = longrepr
    elif isinstance(longrepr, dict):
        crash = longrepr.get("reprcrash")
        if isinstance(crash, dict):
            message = str(crash.get("message") or "")
        else:
            message = str(longrepr.get("message") or "")

    return {
        "nodeid": f"{entry.get('nodeid', 'collection')}::collection_error",
        "outcome": "failed",
        "duration": 0.0,
        "longrepr": {"reprcrash": {"message": message or "collection failed", "reprtraceback": []}},
        "setup": {"outcome": "failed", "duration": 0.0},
        "call": {"outcome": "failed", "duration": 0.0},
        "teardown": {"outcome": "notrun", "duration": 0.0},
        "wasxfail": False,
    }


def _normalise(document: dict[str, Any]) -> dict[str, Any]:
    """Fold collector failures into ``tests`` so no evidence is lost.

    Args:
        document: The raw ``pytest-json-report`` document.

    Returns:
        The same document with every failed collector appended to ``tests``.
        Done in the sandbox rather than in the worker so the mutation is visible
        to anyone inspecting a container's raw output.
    """
    collectors = document.get("collectors")
    if not isinstance(collectors, list):
        return document

    tests = document.setdefault("tests", [])
    already_present = {str(t.get("nodeid")) for t in tests if isinstance(t, dict)}
    for entry in collectors:
        if not isinstance(entry, dict) or entry.get("outcome") != "failed":
            continue
        flattened = _flatten_collect_error(entry)
        if flattened["nodeid"] not in already_present:
            tests.append(flattened)
    return document


def _augment_harness_errors(document: dict[str, Any]) -> dict[str, Any]:
    """Record harness-level problems that pytest reports outside ``tests``.

    A ``KeyboardInterrupt`` or an internal error surfaces in the report's
    ``exitstatus``/``internalErrors`` rather than as a test. Surfacing them here
    keeps the worker from having to interpret two different report shapes.
    """
    errors = document.get("internalErrors")
    harness_errors: list[str] = []
    if isinstance(errors, list):
        for entry in errors:
            if isinstance(entry, dict):
                harness_errors.append(str(entry.get("message") or entry))
            else:
                harness_errors.append(str(entry))
    if harness_errors:
        document["harness_errors"] = harness_errors
    return document


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Rewrite the report before pytest exits.

    Args:
        session: The pytest session. Used only to locate the report path.
        exitstatus: pytest's exit status, recorded for diagnostics.
    """
    report_path = Path(os.environ.get("OA_JSON_REPORT_FILE") or "/tmp/oa-grade-report.json")
    if not report_path.exists():
        _write_diagnostic(
            {
                "error": "no report file produced",
                "report_path": report_path,
                "exitstatus": exitstatus,
                "cwd": str(Path.cwd()),
                "workspace_listing": _safe_listdir("/tmp/workspace"),
                "grading_listing": _safe_listdir("/grading"),
            }
        )
        return

    try:
        document = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _write_diagnostic({"error": f"report unreadable: {exc}", "exitstatus": exitstatus})
        return

    normalised = _augment_harness_errors(_normalise(document))
    report_path.write_text(json.dumps(normalised), encoding="utf-8")


def _safe_listdir(path: str) -> list[str]:
    """List a directory, returning a marker instead of raising.

    Used only for diagnostics, so a permission problem must not itself become an
    unhandled exception inside a pytest hook.
    """
    try:
        return sorted(entry.name for entry in Path(path).iterdir())
    except OSError as exc:
        return [f"<unreadable: {exc}>"]


def _write_diagnostic(payload: dict[str, Any]) -> None:
    """Best-effort write of a diagnostic document. Never raises."""
    with contextlib.suppress(OSError):
        DIAGNOSTIC_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    # Direct invocation is for local debugging only:
    #   docker run --rm -v "$PWD:/tmp/grading" -w /tmp/workspace oa/sandbox-python \
    #     python -m pytest /grading -q
    raise SystemExit("invoke via pytest, not directly")
