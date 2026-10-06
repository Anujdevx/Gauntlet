# Runbook: execution latency above target

Target: **p95 under 3 s** end to end; **under 2 s** in load tests.

## Diagnose in this order

### 1. Warm-pool hit rate

```promql
rate(oa_warm_pool_acquisitions_total{outcome="cold_miss"}[5m])
/
rate(oa_warm_pool_acquisitions_total[5m])
```

Above ~5%, stop and fix the pool. A cold start costs 300-800 ms and is the single
largest term in the budget. Everything else is secondary.

```bash
docker compose exec worker env | grep POOL_SIZE
docker compose logs worker | grep warm_pool_replenish | tail -20
```

`warm_pool_replenish_errors_total` rising means the daemon is refusing
`create_container`. Check `runsc` registration and that the images exist.

### 2. Queue depth

```promql
max(oa_queue_depth)
```

Non-zero and growing means insufficient workers. Scale out:

```bash
docker compose up -d --scale worker=4
```

Note `worker_concurrency` and `pool_size_per_language` multiply: the container
count is `concurrency * pool_size * 3`. On one host, 8 × 4 × 3 = 96 idle
containers. Each is ~256 MiB *capped*, not reserved, so this is usually fine —
but verify against the host's memory before scaling blindly.

### 3. Where the time actually goes

Compare the two timers on a result:

- `runtime_ms` — sandboxed execution only.
- `duration_ms_total` — enqueue to terminal, including queue wait and pool
acquisition.

`runtime_ms` ≈ 400 ms but `duration_ms_total` ≈ 3 s means the time is in
acquisition or queueing, i.e. step 1 or 2 above. Both ≈ 3 s means the runner
itself is slow: check the sandbox image for a heavy import.

### 4. Daemon health

```bash
docker compose exec dind docker info --format '{{json .Runtimes}}'
docker stats --no-stream
```

gVisor's syscall interception adds real overhead — 10-30% over `runc` is normal.
If you have silently fallen back to `runc`, latency will look better and security
is worse. Check the runtime list before celebrating.

## Tuning

| Lever | Effect | Cost |
| --- | --- | --- |
| `POOL_SIZE_PER_LANGUAGE` ↑ | Fewer cold misses | More idle containers per worker |
| `WORKER_CONCURRENCY` ↑ | More parallelism | More memory, more containers |
| Wall clock 30 s → 20 s | Caps worst case | Kills legitimate slow tests |
| `worker_prefetch_multiplier=1` | Fairness across workers | Slightly higher queue latency |

Do **not** raise the wall clock to make a slow question pass. The wall clock is a
containment control, not a tuning knob: a candidate who writes an infinite loop
should be killed at the limit and shown `TIMEOUT`.

## Escalate

If p95 stays above 2 s with a healthy pool at low queue depth, the per-execution
cost needs a profile. That is Phase 1 step 7 (load harness), not a config change.
