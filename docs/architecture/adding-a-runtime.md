# Adding a language runtime

Worked example: adding Go. Six files, two of which are tests.

## Checklist

- [ ] `sandbox/images/Dockerfile.<lang>`
- [ ] `sandbox/runners/<lang>/` — grader entrypoint
- [ ] `sandbox/runners/<lang>/conftest.py` — make collection failures reportable
- [ ] `apps/worker/src/oa_worker/parsers/<lang>_parser.py`
- [ ] `Language` enum member in `engine/src/oa_engine/platform/config.py`
- [ ] `Settings.image_<lang>` + `image_for()` branch
- [ ] Parser dispatch in `oa_worker/parsers/base.py::get_parser`
- [ ] Grader dispatch in `oa_worker/sandbox/harness.py::_grader_argv`
- [ ] Tests: parse a passing doc, a failing doc, a missing doc

## 1. The image

Non-negotiable properties, all of which exist for a reason:

```dockerfile
FROM golang:1.23-alpine

# Build-time only. Moving this below the RUN is the same class of bug as
# PIP_NO_INDEX in Dockerfile.python: an ENV applies to every later layer.
ENV GOPATH=/tmp/go GOCACHE=/tmp/gocache

# Exact pins. A grader whose behaviour shifts with a transitive dependency makes
# grading non-deterministic, which is the one thing this platform promises not to be.
RUN go install github.com/stretchr/testify@v1.9.0

# Must match SANDBOX_UID / SANDBOX_GID, or every container fails to start.
RUN addgroup -g 65532 sandbox && adduser -D -u 65532 -G sandbox sandbox \
 && mkdir -p /workspace /grading /runner \
 && chown sandbox:sandbox /workspace \
 && chmod 0755 /workspace /grading /runner

# Root-owned and go-w. Candidate code must not be able to modify the thing
# that grades it.
COPY --chown=root:root sandbox/runners/go/ /runner/
RUN chown -R root:root /runner && chmod -R go-w /runner && chmod 0755 /runner/*.sh

WORKDIR /workspace
USER 65532:65532
CMD ["sleep", "infinity"]

LABEL oa.sandbox.language="go" oa.sandbox.runtime="runsc"
```

Set `GOPATH`/`GOCACHE` into `/tmp` because the root filesystem is read-only and
Go refuses to build without a writable cache.

Do **not** add: compilers exposed at runtime, an SSH client, a package manager
that can reach the network, or anything carrying a credential.

## 2. The runner

Contract with the worker, identical to the existing three:

- Write `/grading/result.json` in the `pytest-json-report` schema. Reusing that
  schema means the worker needs no fourth normalisation path.
- stdout/stderr are captured, capped, and ANSI-stripped upstream.
- **The exit code is recorded but never used as a verdict.** Per-test booleans
  are the only source of truth.

```sh
#!/bin/sh
# Go grading entrypoint. Runs as cwd=/grading (read-only), so the result file is
# assembled in /tmp and copied out.
set -eu
WORK="/tmp/oa-go-$$"; mkdir -p "$WORK"
cd /workspace
set +e
go test -json ./... > "$WORK/raw.json" 2> "$WORK/stderr.txt"
EXIT=$?
set -e
python3 /runner/translate.py "$WORK/raw.json" "$WORK/result.json" || true
cat "$WORK/stderr.txt" >&2 || true
cat "$WORK/result.json" 2>/dev/null || true
rm -rf "$WORK"
exit "$EXIT"
```

Write a `conftest.py`-equivalent that folds **collection/compile failures** into
the test list. Without it, a submission that fails to build produces an empty
`tests` array, which is indistinguishable from "the grader crashed" and gets
reported to the candidate as `ERROR` instead of "your code did not compile".

## 3. The parser

```python
class GoParser:
    """Normalise Go's test2json output."""

    language = Language.GO

    def parse(
        self,
        *,
        payload,
        result_document,
        stdout,
        stderr,
        exit_code,
        runtime_ms,
        stdout_truncated,
        stderr_truncated,
        submission_id,
    ) -> ExecutionResult: ...
```

Three invariants, all enforced by the shared tests:

1. **No string matching for verdicts.** Use the runner's own `passed`/`failed`.
2. **A missing document is `ERROR` or `INFRA_ERROR`, never `FAILED`.**
3. **Skipped tests are excluded from the score denominator**, so a suite that
   skips everything cannot score 1.0.

Stable test ids matter more than they look: the skill engine attributes a failure
to a specific concept node over time, so `TestFoo/should_bar` must keep its
identity across runs.

## 4. Wiring

Three dispatch sites, all small:

```python
# engine/platform/config.py
class Language(str, enum.Enum):
    GO = "go"

# Settings: image_go: str = "oa/sandbox-go:1.0.0" + a branch in image_for()

# oa_worker/parsers/base.py::get_parser
case Language.GO:
    from oa_worker.parsers.go_parser import GoParser
    return GoParser()

# oa_worker/sandbox/harness.py::_grader_argv
"go": ["/runner/grade-go.sh"],
```

Note `_grader_argv` dispatches on the payload's declared language, not on file
extension — the template author declares the language, and a candidate cannot
influence that field.

## 5. Tests

Minimum, and all three are non-negotiable:

```python
def test_passing_document_yields_passed(): ...
def test_failing_document_yields_failed(): ...
def test_missing_document_is_error_not_failed(): ...
```

That last one is the one people skip, and it is the one that decides whether a
compile error is reported as a candidate failure or as a platform mystery.

Then run:

```bash
make sandbox-images                       # builds your image
make test-tmpfs                           # confirms the envelope still holds
make test-sandbox                         # needs gVisor; skips without it
```

## Checklist before review

- [ ] uid/gid match `SANDBOX_UID`/`SANDBOX_GID`
- [ ] `/runner` is root-owned and not group/other-writable
- [ ] no `ENV` that breaks your own build step
- [ ] every dependency pinned to an exact version
- [ ] result document matches `pytest-json-report`
- [ ] compile/collection failures surface as failing tests
- [ ] exit code recorded, never used as a verdict
- [ ] stable test ids
- [ ] no credentials in the image or its `ENV`
- [ ] the three parser tests above pass
