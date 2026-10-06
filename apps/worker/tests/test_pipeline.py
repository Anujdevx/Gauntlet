"""The generate -> validate -> retry loop, driven by fakes."""

from __future__ import annotations

from conftest import BUGGY, REF, FakeLLM, FakeSandbox, LLMError, LLMOutputError, draft, make_result

from oa_worker.generation.pipeline import generate_question
from oa_worker.generation.sandbox import SandboxUnavailableError

GOOD_BLIND = "def longest(xs):\n    return 'blind'\n"


def run(llm: FakeLLM, sandbox: FakeSandbox, **kw: object):  # type: ignore[no-untyped-def]
    return generate_question(
        llm=llm,
        sandbox=sandbox,
        language="python",
        node_id="python.iterators",
        node_label="Iterators",
        node_description="Lazy evaluation.",
        difficulty="easy",
        existing_titles=["Old title"],
        **kw,  # type: ignore[arg-type]
    )


def test_accepts_a_good_first_draft(sandbox: FakeSandbox) -> None:
    sandbox.when(GOOD_BLIND, make_result(6))
    outcome = run(FakeLLM([draft()], [GOOD_BLIND, GOOD_BLIND]), sandbox)
    assert outcome.accepted and outcome.question is not None
    assert outcome.metrics["attempts"] == 1
    assert outcome.metrics["mutants_killed"] == 3


def test_static_failure_is_fed_back_and_second_attempt_wins(sandbox: FakeSandbox) -> None:
    sandbox.when(GOOD_BLIND, make_result(6))
    bad = draft(test_code="import os\n" + draft().test_code)
    llm = FakeLLM([bad, draft()], [GOOD_BLIND, GOOD_BLIND])
    outcome = run(llm, sandbox)
    assert outcome.accepted and outcome.metrics["attempts"] == 2
    assert "forbidden module 'os'" in llm.prompts[1]  # the rejection reason reached the model
    assert outcome.attempts[0].stage == "static"


def test_gate_failure_feedback_reaches_the_next_prompt(sandbox: FakeSandbox) -> None:
    sandbox.when(BUGGY[0], make_result(6))  # survivor on attempt 1
    llm = FakeLLM([draft(), draft(title="Longest run, retry")])
    outcome = run(llm, sandbox, max_attempts=2)
    assert not outcome.accepted
    assert "PASSES all your tests" in llm.prompts[1]
    assert [a.stage for a in outcome.attempts] == ["gates", "gates"]


def test_schema_error_is_retried_with_feedback(sandbox: FakeSandbox) -> None:
    sandbox.when(GOOD_BLIND, make_result(6))
    llm = FakeLLM([LLMOutputError("missing field"), draft()], [GOOD_BLIND, GOOD_BLIND])
    outcome = run(llm, sandbox)
    assert outcome.accepted
    assert "missing field" in llm.prompts[1]


def test_llm_outage_aborts_instead_of_blaming_the_question(sandbox: FakeSandbox) -> None:
    outcome = run(FakeLLM([LLMError("every model is unavailable")]), sandbox)
    assert not outcome.accepted and "LLM unavailable" in (outcome.aborted or "")


def test_sandbox_outage_aborts_instead_of_blaming_the_question() -> None:
    class Broken(FakeSandbox):
        def run(self, **_: object):  # type: ignore[no-untyped-def,override]
            raise SandboxUnavailableError("docker daemon unreachable")

    outcome = run(FakeLLM([draft()]), Broken())
    assert not outcome.accepted and "sandbox unavailable" in (outcome.aborted or "")


def test_existing_titles_are_in_the_prompt(sandbox: FakeSandbox) -> None:
    sandbox.when(GOOD_BLIND, make_result(6))
    llm = FakeLLM([draft()], [GOOD_BLIND, GOOD_BLIND])
    run(llm, sandbox, max_attempts=1)
    assert "Old title" in llm.prompts[0]


def test_gives_up_after_max_attempts(sandbox: FakeSandbox) -> None:
    sandbox.when(REF, make_result(2))
    outcome = run(FakeLLM([draft(), draft(), draft()]), sandbox, max_attempts=3)
    assert not outcome.accepted and len(outcome.attempts) == 3 and outcome.aborted is None
