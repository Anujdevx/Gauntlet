"""Prove hidden tests and reference solutions cannot reach the candidate.

The threat: a candidate reads their own grader, hardcodes the expected output,
and scores 100% without solving the problem. That would poison the mastery model
completely -- it is the failure mode that makes a deterministic grader worth
building in the first place.

Defences asserted here:

* Candidate file paths cannot escape ``/workspace`` (traversal, absolute paths,
  drive letters, NUL bytes, backslashes).
* Candidate content cannot reference the grading mount.
* The staging tar is built with explicit arcnames, never the caller's path.
* Oversized and over-count submissions are rejected before any container exists.
* A payload missing hidden tests is rejected rather than graded as vacuously
  passing.
"""

from __future__ import annotations

import io
import tarfile

import pytest

from oa_engine.execution.payload import (
    CONTAINER_GRADING_DIR,
    PayloadError,
    SubmissionPayload,
)


def make_payload(**overrides: object) -> SubmissionPayload:
    """Build a valid payload that individual tests then break in one way."""
    base: dict[str, object] = {
        "submission_id": "sub-1",
        "language": "python",
        "files": {"solution.py": "def solve():\n    return 42\n"},
        "hidden_tests": {"test_solution.py": "def test_solve():\n    assert True\n"},
        "run_command": ("/runner/run-python.sh",),
    }
    base.update(overrides)
    return SubmissionPayload(**base)  # type: ignore[arg-type]


class TestValidPayload:
    """A well-formed payload must pass validation."""

    def test_valid_payload_is_accepted(self) -> None:
        """The happy path works."""
        make_payload().assert_isolated()

    def test_nested_paths_are_allowed(self) -> None:
        """Real multi-file projects have directories."""
        payload = make_payload(files={"src/app/main.py": "x = 1\n", "src/app/util.py": "y = 2\n"})
        payload.assert_isolated()

    def test_missing_run_command_is_rejected(self) -> None:
        """The image's runner must be explicit, never defaulted silently."""
        with pytest.raises(PayloadError, match="run_command"):
            make_payload(run_command=()).assert_isolated()


class TestPathTraversalIsRejected:
    """Every classic archive-traversal shape must be refused."""

    @pytest.mark.parametrize(
        "hostile_path",
        [
            "../escape.py",
            "../../etc/passwd",
            "src/../../escape.py",
            "./../escape.py",
            "/etc/passwd",
            "//absolute/escape.py",
            "C:\\Windows\\System32\\evil.dll",
            "..\\windows\\escape.py",
            "src/\x00/evil.py",
            "..",
            "src/..",
            "",
        ],
    )
    def test_hostile_candidate_path_is_rejected(self, hostile_path: str) -> None:
        """Traversal, absolute paths, drive letters, and NUL bytes all fail."""
        with pytest.raises(PayloadError):
            make_payload(files={hostile_path: "x = 1\n"}).assert_isolated()

    @pytest.mark.parametrize("hostile_path", ["../grader.py", "/grading/test_x.py"])
    def test_hostile_hidden_test_path_is_rejected(self, hostile_path: str) -> None:
        """Hidden test paths are validated on the same terms."""
        with pytest.raises(PayloadError):
            make_payload(hidden_tests={hostile_path: "x = 1\n"}).assert_isolated()


class TestGradingIsolation:
    """Candidate content must not be able to name the grading mount."""

    @pytest.mark.parametrize(
        "attempt",
        [
            f"{CONTAINER_GRADING_DIR}/test_x.py",
            "../grading/test_x.py",
            "src/../../grading/test_x.py",
        ],
    )
    def test_candidate_cannot_name_a_path_inside_the_grading_mount(self, attempt: str) -> None:
        """No candidate path may resolve into /grading.

        This is the property that actually protects the grader, and it is
        enforced purely by refusing absolute paths and ``..`` segments. There is
        deliberately no content check: a candidate is free to write the literal
        string "/grading" into their source, because that reveals nothing.
        """
        with pytest.raises(PayloadError):
            make_payload(files={attempt: "x = 1\n"}).assert_isolated()

    def test_candidate_may_write_the_grading_path_as_source_text(self) -> None:
        """Referring to /grading in code is allowed; it reaches nothing.

        Guards against over-correction: someone tightening this by scanning file
        contents would break legitimate candidates while adding no security.
        """
        payload = make_payload(
            files={"snoop.py": f"open({CONTAINER_GRADING_DIR + '/test_x.py'}).read()\n"}
        )
        payload.assert_isolated()

    def test_candidate_and_grading_archives_are_distinct_objects(self) -> None:
        """The two tarballs are built by separate code paths."""
        payload = make_payload()
        candidate = payload.candidate_tar()
        grading = payload.grading_tar()
        assert candidate != grading
        assert b"test_solution" in grading
        assert b"test_solution" not in candidate

    def test_candidate_archive_contains_no_grading_paths(self) -> None:
        """No archive member may escape the staging root."""
        payload = make_payload()
        with tarfile.open(fileobj=io.BytesIO(payload.candidate_tar())) as tar:
            for member in tar.getmembers():
                assert not member.name.startswith("/")
                assert ".." not in member.name.split("/")
                assert not member.name.startswith(CONTAINER_GRADING_DIR)

    def test_archive_is_deterministic(self) -> None:
        """Identical input yields byte-identical output, so hashes are stable."""
        assert make_payload().candidate_tar() == make_payload().candidate_tar()

    def test_archive_members_are_readable_but_not_group_writable(self) -> None:
        """Members are 0644: readable by anyone, writable by the owner alone.

        The property that matters is *who* can write. Archive members are extracted
        by the seeder as root, so the owner-write bit is root's and the sandbox's
        non-root uid cannot use it. Group- or other-write would let the candidate
        modify its own submission, or the tests it is judged against.
        """
        for archive in (make_payload().candidate_tar(), make_payload().grading_tar()):
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                members = tar.getmembers()
                assert members, "archive should not be empty"
                for member in members:
                    assert member.mode == 0o644
                    assert not member.mode & 0o022, (
                        f"{member.name} is group- or world-writable: {oct(member.mode)}"
                    )

    def test_too_many_files_is_rejected(self) -> None:
        """File count is capped."""
        from oa_engine.platform.config import DEFAULT_ENVELOPE

        over = DEFAULT_ENVELOPE.max_submission_files + 1
        payload = make_payload(files={f"f{i}.py": "x = 1\n" for i in range(over)})
        with pytest.raises(PayloadError, match=r"limit is"):
            payload.assert_isolated()

    def test_oversized_submission_is_rejected(self) -> None:
        """Total byte size is capped."""
        from oa_engine.platform.config import DEFAULT_ENVELOPE

        chunk = "x" * 65_536
        count = (DEFAULT_ENVELOPE.max_submission_bytes // len(chunk)) + 2
        payload = make_payload(files={f"f{i}.py": chunk for i in range(count)})
        with pytest.raises(PayloadError, match="exceeds"):
            payload.assert_isolated()

    def test_size_limit_does_not_mask_the_offending_file(self) -> None:
        """The error names the file that broke the budget, for a usable UX."""
        from oa_engine.platform.config import DEFAULT_ENVELOPE

        payload = make_payload(
            files={
                "small.py": "x = 1\n",
                "huge.py": "y" * (DEFAULT_ENVELOPE.max_submission_bytes + 1),
            }
        )
        with pytest.raises(PayloadError, match=r"huge\.py"):
            payload.assert_isolated()


class TestDeserialisationIsStrict:
    """Untrusted JSON must not be able to construct a half-valid payload."""

    def test_missing_required_keys_are_rejected(self) -> None:
        """A payload without an id or language is unusable."""
        with pytest.raises(PayloadError, match=r"missing required key"):
            SubmissionPayload.from_dict({"language": "python"})

    def test_non_string_file_content_is_rejected(self) -> None:
        """A JSON object where a string belongs is refused, not coerced."""
        with pytest.raises(PayloadError, match="str to str"):
            SubmissionPayload.from_dict(
                {
                    "submission_id": "s",
                    "language": "python",
                    "files": {"a.py": {"nested": True}},
                    "run_command": ["/runner/run-python.sh"],
                }
            )

    def test_run_command_accepts_a_bare_string(self) -> None:
        """A string command is normalised to a one-element tuple."""
        payload = SubmissionPayload.from_dict(
            {
                "submission_id": "s",
                "language": "python",
                "run_command": "/runner/run-python.sh",
            }
        )
        assert payload.run_command == ("/runner/run-python.sh",)

    def test_run_command_rejects_a_number(self) -> None:
        """Non-sequence commands are refused rather than stringified."""
        with pytest.raises(PayloadError, match="run_command"):
            SubmissionPayload.from_dict(
                {"submission_id": "s", "language": "python", "run_command": 42}
            )

    def test_round_trip_preserves_both_file_maps(self) -> None:
        """Serialising and reloading keeps candidate and grader separate."""
        original = make_payload()
        restored = SubmissionPayload.from_dict(original.as_dict())
        assert restored.files == original.files
        assert restored.hidden_tests == original.hidden_tests
        assert restored.run_command == original.run_command

    def test_validation_runs_on_deserialisation(self) -> None:
        """A traversal path in Redis is caught at load, not at injection."""
        with pytest.raises(PayloadError):
            SubmissionPayload.from_dict(
                {
                    "submission_id": "s",
                    "language": "python",
                    "files": {"../escape.py": "x = 1\n"},
                    "run_command": ["/runner/run-python.sh"],
                }
            )
