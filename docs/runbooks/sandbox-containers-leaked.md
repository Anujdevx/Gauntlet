# Runbook: leaked sandbox containers

## Symptom

```console
$ docker compose exec dind docker ps -a --filter label=oa.managed=true
CONTAINER   IMAGE                        STATUS
a1b2c3d4    oa/sandbox-python:1.0.0      Up 4 minutes
```

Every sandbox container is single-use and destroyed in a `finally`. One that
survives is a **live hostile-capable process**, because it has had code injected.

## Why it happens

| Cause | Signal | Likelihood |
| --- | --- | --- |
| Worker container was `SIGKILL`ed between create and remove | container `StartedAt` matches a worker restart | most common |
| Daemon unreachable during remove | `warm_pool_remove_failed` in logs | during a dind restart |
| `docker compose down -v` while work was in flight | n/a | maintenance |
| A bug in the destroy path | container age spans many submissions | **investigate** |

## Diagnose

```bash
# 1. How old, and does it have injected code?
docker compose exec dind docker ps -a \
  --filter label=oa.managed=true \
  --format '{{.Names}}\t{{.Status}}\t{{.CreatedAt}}'

# 2. What was it doing?
docker compose exec dind docker logs <name> 2>&1 | tail -20

# 3. Did it ever run candidate code? Grading writes result.json into /grading.
docker compose exec dind docker exec <name> ls -la /workspace /grading

# 4. Which submission?
docker compose exec dind docker inspect <name> \
  --format '{{index .Config.Labels "oa.submission"}}'
```

## Resolve

```bash
# Label-scoped, so nothing else on the daemon is touched.
docker compose exec dind docker ps -aq --filter label=oa.managed=true \
  | xargs -r docker compose exec -T dind docker rm -f
```

Then confirm the destroy path is intact:

```bash
docker compose exec -T worker python -c "
from oa_worker.pool.registry import get_warm_pool
print(get_warm_pool().snapshot())"
```

A pool reporting non-zero idle counts after a drain means the registry is handing
out containers it no longer owns.

## Prevent

- `worker_max_tasks_per_child=200` recycles children; `stop_warm_pool(drain=True)`
  on `worker_process_shutdown` drains each child's pool. If you see a child being
  replaced without its pool drained, that signal handler is not registered.
- CI asserts zero survivors after the adversarial suite. Treat a failure there as
  a release blocker, not a flake.
- Alert on `count(oa_warm_pool_size) > 0` for a pool that should be idle. A
  permanently non-zero idle pool usually means containers are being created and
  never destroyed.
