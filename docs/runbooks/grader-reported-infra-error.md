# Runbook: candidates see "Platform error"

## Why this runbook exists

A `platform error` or `could not run tests` result is **not** a candidate
failure, and it must never be recorded as one. `ExecutionStatus.counts_against_candidate`
is `False` for both, so the BKT model declines to update: a Docker daemon hiccup
does not make the candidate weaker at React.

The cost of getting this wrong is silent. Mastery estimates drift down for
reasons that have nothing to do with ability, and nothing in the UI tells you
why. So: **any non-trivial rate of this metric is a platform incident.**

## Symptom

```
candidates report: "Could not run tests" / "Platform error — not your code"
oa_execution_infrastructure_failures_total is non-zero
```

## Triage

### 1. `INFRA_ERROR`

The orchestrator or the grader failed. The message is in `stderr`.

```bash
docker compose logs worker | jq -c 'select(.event=="execution.infra_error")' | tail
```

| Message | Cause | Fix |
| --- | --- | --- |
| `required runtime 'runsc' is not registered` | gVisor missing | `infra/gvisor/install-runsc.sh` |
| `cannot reach Docker daemon` | dind down | `docker compose ps dind` |
| `cannot create a <lang> sandbox` | image missing | `make sandbox-images` |
| `staged archive ... unreadable` | payload blob vanished from Redis | check Redis `maxmemory-policy` is `noeviction` |
| `no result document produced` | grader crashed or the image lacks the runner | exec into the image and run `grade.sh` by hand |

### 2. `ERROR` with "no result document"

The runner started and died. Common for a **collection failure**: the candidate's
code raised on import, so the grader never produced a report.

Check first whether this is actually a candidate problem:

```bash
docker compose logs worker | jq -c 'select(.submission_id=="<id>")' | tail -30
```

If `stderr` contains an `ImportError`, `SyntaxError`, or a `sqlite3.OperationalError`
from the candidate's own file, the candidate caused it and the *correct* status is
`FAILED`. That promotion is what `sandbox/runners/*/conftest.py` does — it folds
collection errors into the test list. If it is not happening, that runner hook is
not being loaded:

```bash
docker compose exec dind docker run --rm --entrypoint ls \
  oa/sandbox-python:1.0.0 -la /runner/
```

### 3. `RESOURCE_EXCEEDED`

The cgroup killed the process: memory, or `SIGKILL` from the wall clock.

```promql
rate(oa_execution_total{status="resource_exceeded"}[15m])
```

Distinguish memory from timeout by exit code — `137`/`152` are the runtime's
`SIGKILL`/`SIGTERM`, `124` is a `timeout(1)`-style kill. The adapter maps 137/152
to `RESOURCE_EXCEEDED` and 124 to `TIMEOUT`.

A spike usually means the question is unreasonably heavy (a naive O(n²) expected
answer over 10⁵ rows, say) rather than anything malicious. Check the template's
fixture size before assuming an attack.

## Confirm the mastery model stayed clean

```sql
SELECT status, count(*)
FROM submissions
WHERE created_at > now() - interval '1 hour'
GROUP BY status;
```

`infra_error` and `error` rows must **not** have produced `skill_states` updates.
If they have, the guard in the Phase 3 update path is missing — that is a
correctness bug in the engine, not an ops issue.

## Escalate

If `oa_execution_infrastructure_failures_total` exceeds 0.1% of executions over
15 minutes, page. Below that it is noise from a restarting worker; above it the
mastery model is being fed false evidence.
