#!/bin/sh
# Candidate "Run" phase for Python. Executes the submission with NO hidden tests
# present, so a candidate cannot read their grader by printing the directory.
set -eu
cd /tmp/workspace
exec python "${OA_ENTRY_FILE:-solution.py}"
