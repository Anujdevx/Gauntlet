#!/bin/sh
# Python grading entrypoint.
#
# Contract with the worker:
#   * Writes result.json in the pytest-json-report schema to the writable tmpfs.
#   * stdout and stderr are captured, capped, and sanitised upstream.
#   * The exit code is recorded but never used as a verdict; the worker reads
#     per-test booleans from result.json.
#
# Inputs (the hidden tests) live on a read-only volume; outputs (result.json) go to
# the writable tmpfs. That split is what stops the candidate's own "Run" phase from
# planting a passing result: it cannot write to the grading volume at all.
set -eu

RESULT="${OA_RESULT_PATH:-/tmp/grading-out/result.json}"
WORK="/tmp/oa-grade-$$"
# The output directory must exist before pytest writes into it. The grading
# *input* volume is read-only by design, so results cannot land beside the tests.
mkdir -p "$WORK" "$(dirname "$RESULT")"

# Candidate code lives in the workspace volume and must be importable, so it goes
# first on the path. The grading directory follows, which is what lets hidden
# tests import the candidate's modules.
PYTHONPATH="/tmp/workspace:/tmp/grading${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

# Hard cap inside the sandbox as well as outside. Belt and braces: the worker's
# wall clock is the real guarantee, but a test suite that fans out should not
# monopolise the box while it is being torn down.
export PYTEST_ADDOPTS="--timeout=10 --timeout-method=thread"
export PYTHONDONTWRITEBYTECODE=1

cd /tmp/workspace

set +e
python -m pytest /tmp/grading \
    --json-report \
    --json-report-file="$RESULT" \
    --json-report-omit=collectors,log,streams,warnings \
    --tb=short \
    --no-header \
    -p no:cacheprovider \
    2>"$WORK/stderr.txt"
EXIT=$?
set -e

cat "$WORK/stderr.txt" >&2 2>/dev/null || true

# The result document must land at the path the worker reads, even though
# /grading is read-only, so it is assembled in /tmp and moved into place by the
# worker via a second copy. Writing it here keeps the file usable for local

# The result travels on stdout between markers, not via the daemon's archive API.
# `get_archive` (and `docker cp`) reads the container's *root filesystem layer*,
# so it can never see a tmpfs -- and /tmp is a tmpfs here, because the grading
# volume is mounted read-only and the result therefore cannot be written beside the
# tests. Asking the daemon for a tmpfs file returns 404 for a file that
# demonstrably exists, which reads exactly like "the grader never ran".
#
# Markers rather than a bare `cat`: candidate code also controls stdout, so the
# document must be delimited by something a candidate cannot forge.
if [ -f "$RESULT" ]; then
    echo "OA_RESULT_BEGIN"
    cat "$RESULT"
    echo "OA_RESULT_END"
else
    echo "OA_RESULT_BEGIN"
    echo '{"tests": [], "collectors": [], "harness_errors": ["the grader produced no result document"]}'
    echo "OA_RESULT_END"
fi

rm -rf "$WORK"
exit "$EXIT"
