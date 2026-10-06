#!/bin/sh
# React grading entrypoint.
#
# Writes the Vitest JSON report to $OA_RESULT_PATH and echoes it to stdout for
# debugging. The worker parses the JSON; the exit code is recorded but is never
# the verdict.
set -eu

RESULT="${OA_RESULT_PATH:-/tmp/grading-out/result.json}"
export OA_RESULT_PATH="$RESULT"

# The grader's own node_modules are in /runner. They go first on NODE_PATH so a
# candidate cannot shadow React or Vitest with their own copy -- a classic
# attack is shipping a patched @testing-library that always passes.
NODE_PATH="/runner/node_modules"
export NODE_PATH

# Vite writes its cache and dep-optimisation output next to the project by
# default. The root filesystem is read-only, so redirect it to the /tmp tmpfs.
mkdir -p "$(dirname "$RESULT")"
export VITE_CACHE_DIR=/tmp/vite-cache
export TMPDIR=/tmp
mkdir -p "$VITE_CACHE_DIR"

cd /tmp/workspace

set +e
node /runner/node_modules/vitest/vitest.mjs run \
    --config /runner/vitest.config.mjs \
    --root /tmp/workspace \
    "$@" 2>&1
EXIT=$?
set -e

# The result travels on stdout between markers, not via the daemon's archive API.
# `docker cp` / `get_archive` reads the container's *root filesystem layer*, so it
# can never see a tmpfs -- and /tmp is a tmpfs here. Reading the document back
# that way silently returns 404 for a file that demonstrably exists.
#
# Markers rather than a bare `cat`: stdout also carries candidate-controlled
# output, so the document has to be delimited by something the candidate cannot
# forge.
if [ -f "$RESULT" ]; then
    echo "OA_RESULT_BEGIN"
    cat "$RESULT"
    echo "OA_RESULT_END"
else
    echo "OA_RESULT_BEGIN"
    echo '{"tests": [], "collectors": [], "harness_errors": ["the grader produced no result document"]}'
    echo "OA_RESULT_END"
fi

exit "$EXIT"
