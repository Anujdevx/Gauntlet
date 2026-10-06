# Runbooks

Operational procedures. Each one assumes the symptom is real and does not
include "maybe it is fine".

| Runbook | When |
| --- | --- |
| [sandbox-containers-leaked.md](sandbox-containers-leaked.md) | `docker ps` shows `oa-sbx-*` containers that should not exist |
| [execution-latency.md](execution-latency.md) | `oa_execution_duration_seconds` p95 above 2 s |
| [grader-reported-infra-error.md](grader-reported-infra-error.md) | Candidates see "Platform error" or "Could not run tests" |
| [add-a-new-language.md](add-a-new-language.md) | Checklist for a new runtime |

## Triage order

1. **Is it the platform or the candidate?** `oa_execution_infrastructure_failures_total`
   rising means the platform. Anything else is candidate code and is not an
   incident.
2. **Which layer?** `docker compose ps` first. A service that will not start is a
   different problem from one that is running and misbehaving.
3. **Logs are JSON.** `make logs`, then filter on `submission_id` to follow one
   submission end to end.

```bash
docker compose logs -f worker | jq -c 'select(.submission_id)'
```
