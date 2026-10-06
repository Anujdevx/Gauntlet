#!/bin/sh
# Java grading entrypoint.
#
# Contract with the worker (identical to the Python and SQL graders):
#   * The hidden tests are on the read-only /tmp/grading volume; the candidate's files
#     are in /tmp/workspace.
#   * The result is a pytest-json-report-shaped document written to the tmpfs, then
#     emitted on stdout between markers, because the daemon's archive API cannot see a
#     tmpfs and candidate code also controls stdout.
#   * The exit code is recorded but never used as a verdict.
set -eu

RESULT="${OA_RESULT_PATH:-/tmp/grading-out/result.json}"
mkdir -p "$(dirname "$RESULT")"
export OA_RESULT_PATH="$RESULT"

cd /tmp/workspace

set +e
# One JVM does everything (compile in-process, then run JUnit): a second JVM start would
# cost more than the tests. Heap and metaspace are capped explicitly so the JVM cannot
# reserve more than the 256 MiB cgroup allows. The class-data archive (built when the
# image was built) removes most class loading and verification from startup.
java $OA_JVM_FLAGS -cp /runner/junit.jar:/runner/oagrade.jar OaGrade grade /tmp/workspace /tmp/grading "$RESULT" 2>&1
EXIT=$?
set -e

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
