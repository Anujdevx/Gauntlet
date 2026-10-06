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

# Stage the candidate's files and the hidden tests into ONE directory on the tmpfs.
#
# Hidden tests import the candidate's modules with relative paths (`./useThing`), which
# cannot resolve across two separate mounts, and /runner (where the config lives) is
# read-only while Vite insists on writing a temp file next to its config. Staging
# solves both. Order is the security property: the candidate's files go in first and the
# hidden tests and the grader's own config go in last, so a candidate cannot shadow a
# test, ship their own vitest config, or plant a node_modules.
WORK=/tmp/oa-react
rm -rf "$WORK"
mkdir -p "$WORK"
cp -R /tmp/workspace/. "$WORK"/
# A candidate must not add tests of their own: extra always-passing tests would raise
# the pass ratio. Only the hidden tests, staged next, may exist.
find "$WORK" -path "$WORK/node_modules" -prune -o -type f \
     \( -name '*.test.*' -o -name '*.spec.*' \) -exec rm -f {} +
cp -R /tmp/grading/. "$WORK"/
rm -rf "$WORK/node_modules" "$WORK/vitest.config.mjs" "$WORK/vite.config.js" \
       "$WORK/vite.config.ts" "$WORK/vitest.config.ts" "$WORK/vitest.config.js"
ln -s /runner/node_modules "$WORK/node_modules"
cp /runner/vitest.config.mjs "$WORK/vitest.config.mjs"

cd "$WORK"

set +e
node /runner/node_modules/vitest/vitest.mjs run \
    --config "$WORK/vitest.config.mjs" \
    --root "$WORK" \
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
