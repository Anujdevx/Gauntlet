# gVisor setup

## What this does

gVisor's `runsc` is a userspace kernel. The platform requests it as the runtime
for every sandbox container, so that even a successful container-escape
primitive lands inside gVisor's syscall interception layer rather than on the
host kernel.

Two independent layers therefore protect a submission:

1. Container primitives — namespaces, cgroups, seccomp, capabilities.
2. gVisor — the kernel boundary.

Either one alone has had published escapes in some version. The platform
requires both, and refuses to run without the second.

## Running without gVisor

`SANDBOX_RUNTIME` selects the isolation layer and defaults to `runc`, so the
platform starts on any host with a Docker daemon:

```bash
SANDBOX_RUNTIME=runc   make up     # development: one isolation layer
SANDBOX_RUNTIME=runsc  make up     # production: two layers
```

gVisor-specific tests are marked `@pytest.mark.gvisor` and skip unless
`SANDBOX_RUNTIME=runsc`. Everything else — no network, read-only rootfs,
non-root uid, all capabilities dropped, memory and swap bounds, PID limit,
executable tmpfs, read-only grading mount — is identical in both modes, and a test
enforces that by diffing the two.

If `SANDBOX_RUNTIME=runsc` and `runsc` is not registered, the worker refuses to
start rather than falling back to `runc`.

## Why installation cannot be optional

`infra/gvisor/install-runsc.sh` fails loudly. It will not leave the daemon
running with `runc` as the default while the platform believes it is sandboxed
under gVisor — that is a silent, total loss of the second layer, and it would
not surface until someone exploited it.

The orchestrator enforces the same rule from the other side: `SandboxOrchestrator`
reads `Runtimes` from `docker info` at construction and refuses to start if
`runsc` is absent.

## Manual installation

```bash
sudo RUNSC_VERSION=20241021.0.0 ./infra/gvisor/install-runsc.sh
sudo systemctl restart docker
docker info | grep -A3 runtimes
```

Verify:

```bash
docker run --rm --runtime=runsc --network=none --read-only busybox:1.36 /bin/true
```

## Host requirements

| Requirement | Why |
| --- | --- |
| Linux kernel >= 4.19 | gVisor relies on several newer syscalls and namespaces. |
| `--privileged` or a dedicated runner host | Nested container creation for dind. Never expose such a host publicly. |
| No swap, or swap capped at the cgroup limit | `memswap_limit == mem_limit` only holds without swap escape. |
| seccomp/apparmor not blocking `clone3` | gVisor's own sandboxing needs it. |

## macOS and Docker Desktop

macOS cannot run gVisor: it virtualises the whole kernel, and gVisor *is* a
kernel. Two options:

- **Docker Desktop with the `runsc` runtime is not available.** Instead, run the
  stack inside a Linux VM (`infra/compose/README.md` covers a Multipass setup), or
- **Develop with the pool disabled.** Set `POOL_SIZE_PER_LANGUAGE=0` and use the
  `sandbox` marker skip in the test suite. Security tests that assert on the
  *spec* still run and are the ones that matter for catching regressions.

Sandbox tests that require a live daemon skip with an explanatory reason rather
than quietly relaxing their assertions.
