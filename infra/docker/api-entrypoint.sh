#!/bin/sh
# API container entrypoint.
#
# Migrations run here rather than in a separate service so that the process only
# starts serving once the schema it expects exists. A dedicated migrator service
# would need its own success gate and its own ordering edge; getting that wrong
# produces an app that boots against an old schema, which is a worse failure than
# one that refuses to boot.
#
# `upgrade head` is idempotent, so every replica restarting is harmless.
set -eu

echo "[api] applying migrations" >&2
alembic -c /app/apps/api/alembic.ini upgrade head

echo "[api] starting uvicorn" >&2
# exec so uvicorn becomes PID 1 and receives SIGTERM directly. Without it the
# shell stays as PID 1, swallows the signal, and the container waits out its
# stop timeout on every deploy.
exec uvicorn oa_api.main:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8000 \
    --no-server-header \
    --timeout-keep-alive 65 \
    "$@"
