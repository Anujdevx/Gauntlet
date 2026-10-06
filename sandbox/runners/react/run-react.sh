#!/bin/sh
# Candidate "Run" phase for React.
#
# Exposes the candidate's own dev entrypoint with no hidden tests present. A
# `test` script in the submission runs vitest against the candidate's own files,
# which is their business; grading is a separate invocation from grade.sh.
set -eu
cd /tmp/workspace

if [ -f package.json ]; then
    exec npm run --silent start 2>/dev/null || exec npm run --silent dev
fi

echo "no package.json in /workspace; nothing to run" >&2
exit 1
