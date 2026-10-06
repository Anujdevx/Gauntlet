# Runbook: add a new language runtime

The full guide, with rationale for each step, is in
[../architecture/adding-a-runtime.md](../architecture/adding-a-runtime.md). This is
the operational summary.

## Order matters

Do these in sequence. Steps 3 and 4 are pure wiring and will fail confusingly if
step 2 is not already correct.

```bash
# 1. Write the image and the runner.
$EDITOR sandbox/images/Dockerfile.<lang>
$EDITOR sandbox/runners/<lang>/grade.sh

# 2. Prove the image works in isolation, before any platform wiring.
make sandbox-images
docker compose exec -T dind docker run --rm \
  --network=none --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges --user 65532:65532 \
  --memory 256m --memory-swap 256m --pids-limit 128 \
  --mount type=tmpfs,destination=/tmp,tmpfs-size=67108864,tmpfs-mode=1777 \
  --entrypoint /bin/sh oa/sandbox-<lang>:1.0.0 -c 'your grade.sh invocation'

# 3. Add the parser with its three mandatory tests.
# 4. Wire the four dispatch sites.
make check
```

## The image must satisfy these

Non-negotiable, and each corresponds to a live failure mode:

| Requirement | Symptom if violated |
| --- | --- |
| uid/gid `65532` | every container fails to start: `invalid user` |
| `/runner` root-owned, `go-w` | a candidate can replace the grader |
| No build-time-breaking `ENV` above an install step | `PIP_NO_INDEX` / `NPM_CONFIG_OFFLINE` bit us; see `Dockerfile.python` |
| Exact version pins | grading drifts between runs |
| Cache/temp dirs under `/tmp` | the build or run fails against a read-only rootfs |
| No credentials in `ENV` | CI's `test_environment_carries_no_secrets` fails |

## Verify containment did not regress

```bash
make check        # 88 unit tests, no Docker needed
make test-tmpfs   # 6 live tests: exec-from-/tmp, read-only rootfs, size cap
make test-sandbox # needs gVisor; skips with a reason without it
```

`make test-tmpfs` is the one that matters most after an image change: it proves
your image still allows `/tmp` execution and still refuses a rootfs write. A new
base image can silently reintroduce the noexec trap.

## Ship

```bash
git tag sandbox-<lang>-1.0.0
```

Then record the digest in `ExecutionResult.image_digest`, which the parser path
already populates. Grades must be reproducible against an exact image, and a
mutable tag means a submission graded today could have been graded by different
grader code last month — silently invalidating historical mastery estimates.
