#!/bin/sh
# Candidate "Run" phase for SQL.
#
# Executes the submitted query against a scratch in-memory database and prints
# the rows. NO hidden tests are present, so a candidate cannot learn the
# expected output by running anything here.
set -eu

python3 - <<'PY'
import sqlite3, sys, os

path = "/tmp/workspace/solution.sql"
if not os.path.exists(path):
    print("no /tmp/workspace/solution.sql found", file=sys.stderr)
    raise SystemExit(1)

with open(path, encoding="utf-8") as handle:
    sql = handle.read().strip()
if not sql:
    print("/workspace/solution.sql is empty", file=sys.stderr)
    raise SystemExit(1)

conn = sqlite3.connect(":memory:")
try:
    rows = conn.execute(sql).fetchall()
    if not rows:
        print("(0 rows)")
    for row in rows[:100]:
        print(tuple(row))
    if len(rows) > 100:
        print(f"... {len(rows) - 100} more rows")
finally:
    conn.close()
PY
