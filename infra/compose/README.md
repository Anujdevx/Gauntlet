# Local environment

> The compose file lives at the repository root: [`docker-compose.yml`](../../docker-compose.yml).
> It is not duplicated here. Two copies of a 200-line service definition drift,
> and the one that drifts is the one nobody remembers to update.


## Requirements

- Docker Engine 24+ with Compose v2.
- A Linux host for live sandbox execution. macOS and Windows cannot run gVisor;
  see [../gvisor/README.md](../gvisor/README.md).

## First run

```bash
cp .env.example .env
make up          # docker compose up --build -d
make logs        # tail the api, worker, and dind
make health      # curl the probes
```

Compose builds the three sandbox images once, inside the dind daemon, via the
`sandbox-images` one-shot service. If that service fails, the stack does not
come up: a platform that cannot sandbox correctly should not be reachable.

## Why dind, and what it costs

The worker needs a Docker daemon to create sandboxes. Two options:

- **Socket mount** to the host daemon (`-v /var/run/docker.sock:/var/run/docker.sock`).
  The sandbox containers then run alongside your other containers.
- **Docker-in-Docker.** A nested daemon dedicated to sandboxes.

The compose file uses dind for the nested daemon, and mounts the socket only so
the worker process can reach it. This buys real separation: a sandbox escape
lands inside a throwaway nested daemon, not next to the dev database.

The cost is that the nested container must be `privileged`. That is contained —
it holds only sandbox images and no secrets — but it is a genuine risk on a
shared machine. **Never run this compose file on a host you do not control.**

For production, replace dind with a dedicated runner fleet behind a socket
proxy that only permits `create`/`start`/`exec`/`stop`/`remove`. See
[../../docs/security/sandbox.md](../../docs/security/sandbox.md).

## Service topology

```
              +---------+
   browser ---|   web   |   Next.js, no privileges
              +---------+
                 |  HTTP / WS
              +---------+
              |   api   |   no Docker socket, cannot execute code
              +---------+
                 |  enqueue
              +---------+
              |  redis  |   broker + result backend + live event streams
              +---------+
                 |
              +---------+
              | worker  |   ONLY holder of a Docker socket
              +---------+
                 |  tcp://dind:2375
              +---------+
              |  dind   |   nested daemon, privileged, holds sandbox images
              +---------+
                 |  creates
      +----------+----------+
      |          |          |
   python      react       sql     single-use sandboxes, runsc, sealed
      +----------+----------+
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `required runtime 'runsc' is not registered` | gVisor missing or not the default runtime | `infra/gvisor/install-runsc.sh` |
| `cannot create python sandbox from oa/sandbox-python` | Images not built | `make sandbox-images` |
| `/health/ready` reports `degraded` | One dependency down | Check `postgres` and `redis` container logs; degraded still serves traffic by design |
| Submissions sit `pending` forever | Worker cannot reach Redis | `docker compose exec worker python -c "import redis; redis.Redis.from_url('$REDIS_URL').ping()"` |
| Sandbox tests all skip | No runsc | Expected on macOS; the spec-level security tests still run |

## Data

`docker compose down` keeps volumes. `docker compose down -v` destroys the
database, the queue, and the cached sandbox images.
