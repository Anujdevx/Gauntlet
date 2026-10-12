"""Static checks on Java drafts. Compilation itself is checked by the sandbox gates."""

from __future__ import annotations

import pytest

from oa_engine.generation import DraftError, JavaDraft, build_java_question
from oa_engine.generation.prompts import authoring_prompt, blind_prompt

REF = "public class Solution {\n  public static int twice(int x) { return x * 2; }\n}\n"
START = (
    "public class Solution {\n  public static int twice(int x) {\n"
    '    throw new UnsupportedOperationException("TODO");\n  }\n}\n'
)
BUGGY = [
    "public class Solution {\n  public static int twice(int x) { return x + 2; }\n}\n",
    "public class Solution {\n  public static int twice(int x) { return x; }\n}\n",
    "public class Solution {\n  public static int twice(int x) { return -x * 2; }\n}\n",
]
TESTS = """import static org.junit.jupiter.api.Assertions.assertEquals;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class SolutionTest {
    @Test void zero() { assertEquals(0, Solution.twice(0)); }
    @Test void one() { assertEquals(2, Solution.twice(1)); }
    @Test void negative() { assertEquals(-4, Solution.twice(-2)); }
    @ParameterizedTest @CsvSource({"3,6", "5,10"})
    void table(int in, int out) { assertEquals(out, Solution.twice(in)); }
}
"""


def draft(**over: object) -> JavaDraft:
    base: dict[str, object] = {
        "title": "Double a number",
        "prompt": "Implement `Solution.twice(int x)` returning x multiplied by two. " * 3,
        "starter_code": START,
        "reference_code": REF,
        "test_code": TESTS,
        "buggy_solutions": BUGGY,
    }
    base.update(over)
    return JavaDraft.model_validate(base)


def test_happy_path() -> None:
    q = build_java_question(draft(), difficulty="easy")
    assert q.language == "java"
    assert set(q.starter_files) == {"Solution.java"}
    assert set(q.hidden_tests) == {"SolutionTest.java"}
    assert q.run_command == ("/bin/sh", "/runner/run-java.sh")
    assert q.meta["test_methods"] == 4


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"test_code": TESTS.replace("class SolutionTest", "class Other")}, "class SolutionTest"),
        ({"test_code": TESTS.replace("org.junit.jupiter", "org.junit")}, "JUnit 5"),
        (
            {
                "test_code": (
                    "import org.junit.jupiter.api.Test;\nclass SolutionTest { @Test void a() {} }"
                )
            },
            "@Test methods",
        ),
        (
            {"test_code": TESTS.replace("@Test void zero", "@Disabled @Test void zero")},
            "skipped/aborted",
        ),
        ({"test_code": TESTS + "\n// Thread.sleep(5)"}, "sleeping"),
        (
            {
                "test_code": TESTS.replace(
                    "assertEquals(0,", "assertEquals(new java.util.Random().nextInt(),"
                )
            },
            "randomness",
        ),
        ({"reference_code": REF.replace("return", "System.exit(0); return")}, "exiting the JVM"),
        ({"reference_code": "package x;\n" + REF}, "default package"),
        ({"starter_code": "class Solution {}"}, "public class Solution"),
        (
            {
                "reference_code": REF.replace(
                    "return x * 2", "return java.nio.file.Files.size(null)"
                )
            },
            "file access",
        ),
        ({"starter_code": REF}, "identical to reference_code"),
        ({"buggy_solutions": [BUGGY[0], BUGGY[0], BUGGY[1]]}, "duplicates"),
        ({"buggy_solutions": [REF, BUGGY[0], BUGGY[1]]}, "identical to reference_code"),
    ],
)
def test_rejections(override: dict[str, object], fragment: str) -> None:
    with pytest.raises(DraftError) as caught:
        build_java_question(draft(**override), difficulty="easy")
    assert fragment in " ".join(caught.value.problems)


def test_prompts_carry_the_java_contract_and_target() -> None:
    prompt = authoring_prompt(
        language="java",
        node_id="java.collections",
        node_label="Collections",
        node_description="d",
        difficulty="easy",
        existing_titles=[],
        feedback=None,
    )
    assert "JUnit 5" in prompt and "DEFAULT" in prompt
    assert "Solution.java" in blind_prompt(language="java", prompt="p", starter="s")


# ---------------------------------------------------------------- DSA / language resolution


def test_language_resolution() -> None:
    from oa_engine.generation import resolve_language

    assert resolve_language("sql.joins.outer") == "sql"
    assert resolve_language("java.streams") == "java"
    assert resolve_language("dsa.arrays.two_pointers") == "python"  # default
    assert resolve_language("dsa.arrays.two_pointers", "java") == "java"
    with pytest.raises(ValueError, match="cannot be authored in"):
        resolve_language("sql.joins", "python")
    with pytest.raises(ValueError, match="not implemented"):
        resolve_language("rag.retrieval")
    with pytest.raises(ValueError, match="DSA questions"):
        resolve_language("dsa.dp.one_dimensional", "sql")


@pytest.mark.parametrize("language", ["python", "java"])
def test_dsa_prompt_adds_algorithmic_guidance_only_for_dsa(language: str) -> None:
    def build(node: str) -> str:
        return authoring_prompt(
            language=language,
            node_id=node,
            node_label="n",
            node_description="d",
            difficulty="medium",
            existing_titles=[],
            feedback=None,
        )

    dsa = build("dsa.arrays.sliding_window")
    assert "DSA GUIDELINES" in dsa
    assert "ONE large-input" in dsa and "NEVER require a custom" in dsa
    assert "DSA GUIDELINES" not in build("python.iterators")
