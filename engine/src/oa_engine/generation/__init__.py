"""LLM-assisted question generation, validated deterministically.

The LLM authors a question and its hidden tests. Nothing it writes is trusted until
sandbox gates (see ``oa_worker.generation``) prove the tests are correct. Grading
itself stays 100% deterministic: an LLM never decides whether code passes.
"""

from oa_engine.generation.drafts import (
    DraftError,
    JavaDraft,
    PythonDraft,
    Question,
    SqlDraft,
    build_java_question,
    build_python_question,
    build_sql_question,
)
from oa_engine.generation.languages import resolve_language

__all__ = [
    "DraftError",
    "JavaDraft",
    "PythonDraft",
    "Question",
    "SqlDraft",
    "build_java_question",
    "build_python_question",
    "build_sql_question",
    "resolve_language",
]
