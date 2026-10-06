"""Adapter for the Java grader.

``OaGrade`` (see ``sandbox/runners/java``) runs JUnit 5 and writes a
pytest-json-report-shaped document, with a compile failure reported as a failed
"collector". That is deliberate: it lets Java reuse the pytest adapter's mapping,
including its rule that a missing or unreadable document is an ``ERROR`` (not a
candidate failure) and that only a literal ``passed`` counts as a pass.
"""

from __future__ import annotations

from oa_engine.platform.config import Language
from oa_worker.parsers.pytest_parser import PytestParser

__all__ = ["JavaParser"]


class JavaParser(PytestParser):
    """Normalise ``OaGrade`` output."""

    language = Language.JAVA
