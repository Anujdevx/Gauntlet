#!/bin/sh
# SQL grading entrypoint.
#
# Runs the generated unittest.TestCase against a fresh in-memory database and
# writes a pytest-json-report-shaped document so the worker's SQL adapter can
# reuse one normalisation path.
#
# Runs as cwd=/grading (read-only), so the result file is assembled in /tmp and
# copied out. The read-only grading mount is what stops the candidate's own
# "Run" phase from planting a passing result.
set -eu

RESULT="${OA_RESULT_PATH:-/tmp/grading-out/result.json}"
WORK="/tmp/oa-sql-$$"
mkdir -p "$WORK" "$(dirname "$RESULT")"

PYTHONPATH="/runner:/tmp/workspace${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
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
