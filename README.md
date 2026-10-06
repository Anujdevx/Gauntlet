# Adaptive Technical Assessment Simulator

A platform that turns a job description into a graded, adaptive engineering
assessment: it decomposes a syllabus into a DAG of granular skills, serves
practical coding tasks in a zero-trust sandbox, grades them with hidden unit
tests only, and uses the results to update a probabilistic model of the
candidate's mastery.

> **Status: Phase 1 (RCE engine) in progress.** Backend scaffold, sandbox
> orchestrator, and warm pool are implemented. See [docs/architecture](docs/architecture/overview.md).

## Core principles

| Principle | Consequence |
| --- | --- |
| Practical engineering tasks, not abstract puzzles | Every question ships with hidden tests that exercise a real code path. |
| Grading is 100% deterministic | Hidden unit tests only. **An LLM never grades code.** |
| LLMs do two jobs only | Mutating *presentation* of a curated template, and post-submission interview feedback. Neither can affect a pass/fail grade. |
| Every submission is hostile | Containers are single-use, network-free, read-only-root, non-root, cap-dropped, under gVisor. |
| Adaptivity is probabilistic, not rule-based | BKT per skill node; Elo for global ability and difficulty calibration. |

## Threat model, one line

The API never executes code and holds no Docker socket. Workers hold the socket
and hand every submission to a container that cannot reach the network, the host
filesystem, or any secret. If a sandbox does break, the blast radius is one
single-use container with no credentials, on a host whose kernel is not directly
reachable.

## Quickstart

```bash
cp .env.example .env
make up          # build + start, then block until /health/ready is ok
```

Then:

- API — <http://localhost:8000/docs>
- Metrics — <http://localhost:8000/metrics>
- Frontend — <http://localhost:3000>

`make up` also builds the three sandbox images. To do it separately, or after
changing a `sandbox/images/Dockerfile.*`:

```bash
make sandbox-images
```

## Verifying it

```bash
make check       # ruff, ruff format, mypy strict, pytest  (no Docker needed)
make test-tmpfs  # 6 live tests: exec-from-/tmp + read-only rootfs (needs Docker)
make test-sandbox# adversarial suite; needs gVisor, skips with a reason without it
```

`make test-tmpfs` runs against the `dind` daemon, which publishes its Docker API
on `127.0.0.1:2375` for exactly this purpose.

> **macOS cannot run gVisor.** Docker Desktop virtualises the kernel and gVisor *is*
> a kernel, so `make test-sandbox` always skips there. Image builds and the
> container-spec tests work fine; the adversarial suite needs a Linux runner. See
> [infra/gvisor/README.md](infra/gvisor/README.md).

## Local development without Docker Compose

```bash
uv sync --all-packages
uv run ruff check .
uv run mypy apps engine
uv run pytest
uv run uvicorn oa_api.main:create_app --factory --reload
uv run celery -A oa_worker.celery_app worker --loglevel=info
```

## Repository layout

```
apps/web        Next.js frontend: Monaco IDE, dashboards, live test streaming
apps/api        FastAPI control plane. Enqueues and streams; never executes.
apps/worker     Celery workers, sandbox orchestrator, warm pool, result parsers
sandbox/images  Dockerfile.{react,sql,python} with all dependencies baked in
sandbox/runners Per-language test-runner entrypoints
sandbox/tests   Adversarial security tests; every escape attempt must be contained
engine          BKT, Elo, DAG blame, adaptive retrieval, LLM mutation validation
infra           docker-compose, gVisor setup, CI workflows
templates       Curated seed question templates with hidden tests
docs            Architecture, security model, runbooks, API reference
```

## Documentation

- [Architecture overview](docs/architecture/overview.md) — components, trust boundaries, latency budget
- [Sandbox security model](docs/security/sandbox.md) — all 20 controls and how each is verified
- [Adding a language runtime](docs/architecture/adding-a-runtime.md) — worked example, six files
- [API reference](docs/api/reference.md) — endpoints and metrics
- [Runbooks](docs/runbooks/) — leaked containers, latency, infra errors, new runtimes

## License

Proprietary. All rights reserved.