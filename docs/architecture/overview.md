# Architecture

## The one-paragraph version

The API never executes candidate code and holds no Docker socket. It accepts a
submission, stores it, and hands an opaque `submission_id` to the broker. Workers
are the only process with a container runtime, and they place every submission
into a single-use container that cannot reach the network, the host filesystem,
or any credential, under gVisor. Grading is a binary per hidden test, computed by
the runner inside that container. An LLM touches the system in exactly two
places, neither of which can affect a pass/fail grade.

## Components

```
                    ┌──────────────┐
   browser ────────▶│     web      │  Next.js. Monaco IDE, dashboards.
                    │  (no privs)  │  Cannot execute anything.
                    └──────┬───────┘
                           │ HTTPS / WSS
                    ┌──────▼───────┐
                    │     api      │  FastAPI control plane.
                    │              │  NO Docker SDK installed.
                    │              │  NO celery dependency.
                    └──────┬───────┘
                           │ enqueue (opaque id)        ┌──────────┐
                           └───────────────────────────▶│  redis   │
                           ┌──────────────┐             │ broker + │
                           │   postgres   │             │ results +│
                           │  + pgvector  │             │ streams  │
                           └──────────────┘             └────┬─────┘
                                                              │
                    ┌─────────────────────────────────────────▼──────┐
                    │                  worker (Celery)              │
                    │  THE ONLY HOLDER OF A DOCKER SOCKET           │
                    │                                                 │
                    │  warm pool ──▶ acquire ──▶ inject ──▶ grade     │
                    │      ▲                              │          │
                    │      └────────── replenish ─────────┴─ destroy │
                    └───────────────────────────────┬─────────────────┘
                                                    │ tcp://dind:2375
                    ┌───────────────────────────────▼─────────────────┐
                    │              dind (nested daemon)               │
                    │  privileged. Holds only sandbox images.        │
                    └───────────────────────────────┬─────────────────┘
                                                    │ creates, single-use
                        ┌───────────────────────────┼───────────────────────────┐
                        ▼                           ▼                           ▼
                 ┌─────────────┐             ┌─────────────┐             ┌─────────────┐
                 │   python    │             │    react    │             │    sql      │
                 │  sandbox    │             │   sandbox   │             │  sandbox   │
                 │ runsc, ro   │             │ runsc, ro   │             │ runsc, ro   │
                 │ rootfs, no  │             │ rootfs, no  │             │ rootfs, no  │
                 │ net, /tmp   │             │ net, /tmp   │             │ net, /tmp   │
                 │ exec        │             │ exec        │             │ exec        │
                 └─────────────┘             └─────────────┘             └─────────────┘
```

## Why the API cannot execute code

Not policy — absence. The API image installs `oa-api`, whose dependency graph
contains neither `docker` nor `celery`. Verified:

```console
$ docker compose exec api python -c "import docker"
ModuleNotFoundError: No module named 'docker'
$ docker compose exec api python -c "import celery"
ModuleNotFoundError: No module named 'celery'
$ docker compose exec api ls /var/run/docker.sock
ls: cannot access '/var/run/docker.sock': No such file or directory
```

This matters because "the web server should not execute untrusted code" is a
sentence that decays the first time someone needs a feature. Making it
architecturally impossible turns a review-time check into a build-time fact.

## Trust boundaries

| Boundary | Crossed by | Trust assumption |
| --- | --- | --- |
| browser → api | HTTPS + session | Candidate-controlled input |
| api → redis | broker protocol | Opaque ids only; no code, no tests |
| worker → dind | Docker API | Worker is trusted compute |
| dind → sandbox | container create | **Submission is hostile** |
| sandbox → anything | — | No route exists |

The last row is the whole product. Everything above it is conventional
engineering; everything below it is assumed adversarial.

## Layered containment

Two independent layers, either of which has had published escapes in some version:

1. **Container primitives** — namespaces, cgroups, seccomp, capabilities, read-only rootfs.
2. **gVisor** — a userspace kernel, so a successful container-escape primitive
   lands in gVisor's syscall interception rather than on the host kernel.

On top of those, three more that are cheap and independent:

3. **No credentials exist anywhere in the sandbox.** Nothing to steal. Enforced by a literal dict in `spec.py` and asserted by `test_environment_carries_no_secrets`.
4. **Single-use containers.** Nothing persists between submissions, so a foothold cannot be reused.
5. **No network.** Loopback only.

Layer 1 is unconditional. Layer 2 is selected by `SANDBOX_RUNTIME` — `runc`
(default, development) or `runsc` (production). The worker refuses to start if the
*configured* runtime is not registered, so a `runsc` production deployment cannot
silently run with one layer. Every other control is identical in both modes,
which a test enforces by diffing them. See
[../security/sandbox.md](../security/sandbox.md).

## Determinism

Grading is a binary per hidden test. Three rules hold across all three languages:

- **Verdicts come from the runner's own per-test booleans**, never from exit codes or string matching. A candidate who prints `0 failed` has not passed anything.
- **A missing or unparseable result document is `ERROR`, not `FAILED`.** "We could not determine the outcome" must never be recorded as a candidate failure.
- **Infrastructure failures are not candidate failures.** `INFRA_ERROR` and `RESOURCE_EXCEEDED` are distinct terminal states so the mastery model can decline to update. Conflating them would teach BKT that the candidate is weak because our Docker daemon hiccuped.

## Where the latency budget goes

Target: p95 under 3 s end to end, under 2 s in load tests.

| Stage | Budget | Mechanism |
| --- | --- | --- |
| API accept + enqueue | ~20 ms | Async, no Docker calls |
| Queue wait | ~0-200 ms | `worker_prefetch_multiplier=1` for fairness |
| Container create + start | **300-800 ms** | **Eliminated by the warm pool** |
| Inject + execute + parse | ~200-800 ms | Pre-baked images, no install step |
| **Total** | **< 2 s warm** | |

Cold starts are the entire risk, which is why the pool exists and why it is
replenished on a background thread: a missed replenishment costs latency, not
correctness.

## Data flow of one submission

```
1. candidate submits  ──▶ api: validate, store payload in redis db2, enqueue {submission_id}
2. worker dequeues    ──▶ orchestrator.run()
3.                     ──▶ acquire warm container (or cold-create)
4.                     ──▶ put_archive candidate files → /workspace
5.                     ──▶ exec candidate's own command      (no tests present)
6.                     ──▶ put_archive hidden tests    → /grading   (read-only)
7.                     ──▶ exec image grader; parse JSON → unified result
8.                     ──▶ destroy container unconditionally; replenish pool
9.                     ──▶ publish result to redis stream; store in db1
10. web streams result over WebSocket (polling fallback)
11. Phase 3: BKT updates the candidate's skill nodes
```

Step 5 and step 7 are separate executions, in that order. The candidate's code has
already exited before hidden tests exist in the container, so it cannot read,
log, or tamper with them.

## Repository layout

```
apps/web        Next.js frontend
apps/api        FastAPI control plane
apps/worker     Celery workers, orchestrator, warm pool, parsers
sandbox/images  Dockerfile.{python,react,sql}
sandbox/runners Per-language graders (python/, react/, sql/)
sandbox/tests   Security tests: envelope, payload isolation, tmpfs exec
engine          platform/ (config, logging, metrics), execution/ (result schema),
                bkt/, elo/, dag/, retrieval/, mutation/  [Phases 2-3]
infra           compose, gvisor, docker, ci
templates       Curated question templates [Phase 3]
docs            This directory
```

`engine/platform/` exists because configuration, logging, and metrics must have
exactly one implementation. Duplicating the security envelope across the API and
the worker is how it ends up differing between the process that thinks it is
enforcing it and the process that is.

## Next

- [../security/sandbox.md](../security/sandbox.md) — controls table, threat model
- [adding-a-runtime.md](adding-a-runtime.md) — adding a language
- [../api/reference.md](../api/reference.md) — endpoints
- [../runbooks/](runbooks/) — operational procedures