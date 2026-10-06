# Sandbox security model

Scope: what a submission can and cannot do, which control stops it, and how that
control is verified.

## Adversary model

The submission is **fully hostile and fully aware of the platform's design**.
Assume the attacker:

- has read this document and the source;
- knows the hidden tests exist and wants to read them;
- will try to reach the network to exfiltrate them;
- will try to write to the image, the runner, or the grading mount;
- will fork-bomb, memory-bomb, and flood output;
- will probe for container-escape primitives;
- will try to read environment variables looking for credentials.

Assume nothing about good faith, and specifically **do not** assume the attacker
is trying to pass the test. Someone who fails every test costs us nothing; someone
who forges a pass costs us the entire mastery model.

## Controls

| # | Control | Where | Verified by |
| --- | --- | --- | --- |
| 1 | `network_disabled` + `NetworkMode=none` | `spec.py` | `test_network_is_disabled`, adversarial egress probe |
| 2 | `ReadonlyRootfs=true` | `spec.py` | `test_root_filesystem_is_read_only_in_the_same_container` |
| 3 | `user=65532:65532` (non-root) | `spec.py` | `test_runs_as_non_root`, `test_identity_comes_from_a_non_root_uid` |
| 4 | `CapDrop=["ALL"]` | `spec.py` | `test_all_capabilities_dropped` |
| 5 | `SecurityOpt=["no-new-privileges"]` | `spec.py` | `test_no_new_privileges` |
| 6 | `PidsLimit=128` | `spec.py` | `test_pids_limit_defends_against_fork_bomb`, live fork bomb |
| 7 | `Memory`=`MemorySwap`=256 MiB, `MemorySwappiness=0` | `spec.py` | `test_memory_limit_and_no_swap_escape`, live memory bomb |
| 8 | `NanoCpus=0.5` | `spec.py` | `test_cpu_quota_is_bounded` |
| 9 | `IpcMode=none` | `spec.py` | `test_namespace_isolation` |
| 10 | `PidMode` not host | `spec.py` | `test_pid_namespace_is_not_shared_with_the_host` |
| 11 | runtime selectable; `runsc` in production | `Settings.sandbox_runtime` | `test_gvisor_guard_*`, `-m gvisor` |
| 12 | `/tmp` tmpfs, 64 MiB, sticky, **exec** | `spec.py` | `test_tmpfs_exec.py` (6 live tests) |
| 13 | `/grading` bind, **read-only** | `spec.py` | `test_workspace_is_writable_and_grading_is_read_only` |
| 14 | No secrets in env | `spec.py` | `test_environment_carries_no_secrets` |
| 15 | Hidden tests injected only after the candidate phase | `harness.py` | two-phase execution, `test_payload_isolation.py` |
| 16 | Path traversal refused | `payload.py` | 12 traversal cases in `test_payload_isolation.py` |
| 17 | Hard wall clock + SIGKILL | `harness.py` | Phase 1 step 6 |
| 18 | Output caps, truncation flagged | `harness.py`, `result.py` | `test_result_scoring_is_deterministic` |
| 19 | Single-use containers, destroyed in `finally` | `orchestrator.py` | pool `release`, CI leak check |
| 20 | Warm containers carry **no bind mounts** | `spec.py` | `test_warm_pool_container_has_no_bind_mounts` |

## The three controls worth explaining

### 12 — `/tmp` must be executable

Section 5 of the specification calls for a memory-backed tmpfs `/tmp` with exec
permitted, because question templates legitimately compile and invoke helper
binaries from a temp directory.

This is harder than it looks, and the reason is worth recording:

| Request form | Result |
| --- | --- |
| `docker.types.Mount(..., tmpfs=TmpfsOptions())` | `noexec` |
| `Type: tmpfs` Mount entry, `TmpfsOptions` without `Noexec` | `noexec` |
| `Type: tmpfs` Mount entry, `Noexec: false` | `noexec` |
| `HostConfig.Tmpfs = {"/tmp": "rw,exec,nosuid,nodev,size=...,mode=1777"}` | **exec permitted** |

The daemon injects `noexec` after parsing `TmpfsOptions`, so the omission trick
does not work — you have to state `exec` in an options string. Both the working
and the broken form are pinned by tests, so a future Docker change that removes
the trap will be noticed rather than silently taken advantage of.

`nosuid` and `nodev` are requested alongside `exec`. The goal is exec permission,
not a permissive `/tmp`, and no question template needs a setuid binary or a
device node from a temp directory.

### 15 — Hidden tests arrive after the candidate has finished

The two phases are separate `exec` calls:

1. `/workspace` gets the submission. `/grading` is empty. The candidate's own
   "Run" command executes here. Anything they print about `/grading` is empty.
2. The candidate's process has exited. Hidden tests are injected at `/grading`,
   which is mounted read-only. The image's grader runs.

A single-phase design — inject everything, then run the candidate — would leak the
grader to anyone who reads a directory listing.

### 20 — Warm containers have no bind mounts at all

A pooled container is created before any submission exists and is reused across
many of them. If it carried a bind mount, it could hold a previous submission's
files: one candidate could read another's work, or a stale grading bundle could
survive into the next grading run. Omitting the binds makes that structurally
impossible rather than a matter of careful cleanup. This is asserted directly.

## Secrets

There are none, and that is the control.

The sandbox environment is a literal dict in `spec.py`. No LLM key, no database
DSN, no cloud metadata credential. `test_environment_carries_no_secrets` fails on
any variable name containing `key`, `token`, `secret`, `password`, `credential`,
`dsn`, or `://` — so adding one "temporarily" is a red build rather than an
incident.

Consequences worth stating:

- RAG and LLM challenges ship **local mocks**. Tests assert on retrieval
  orchestration, prompt formatting, and context-window handling, which is both
  deterministic and safe.
- The Python sandbox image deliberately excludes `torch`,
  `sentence-transformers`, and the OpenAI/Anthropic clients: ~2 GB and a large
  native attack surface for something a candidate never needs, since embedding
  generation belongs in the control plane.

Note that the *base image's* `ENV` is inherited by Docker and cannot be unset via
the create API. `python:3.11-slim` carries `GPG_KEY`, a public fingerprint and
harmless — but inheritance is real, which is why the no-secrets rule is enforced
on our own images at review time rather than assumed.

## Fail-closed behaviour

When a control cannot be applied, the platform refuses to run rather than degrade:

| Situation | Behaviour |
| --- | --- |
| `SANDBOX_RUNTIME=runsc`, `runsc` not registered | `SandboxOrchestrator.__init__` raises `OrchestratorError`. **Never** falls back to `runc`. |
| `SANDBOX_RUNTIME` is not `runc`/`runsc` | `Settings` validation fails at boot, naming the valid values. |
| `SANDBOX_RUNTIME=runc` in production | Allowed, but logs `gvisor_disabled_single_isolation_layer` at warning. CI asserts the guard. |
| Sandbox image missing | `RuntimeError` naming the image and the fix. |
| Docker daemon unreachable | `OrchestratorError` at construction. |
| Container removal fails | Escalates to a label-scoped prune, logs at error level. |
| Grader produced no result document | `ERROR` or `INFRA_ERROR`. **Never** `FAILED`. |
| Test suite skips for lack of gVisor | Skips with a reason string. Assertions are **not** relaxed. |

The last row matters: a security test that quietly loosens its checks on a
developer laptop and tightens them in CI is a test whose only real run is in one
job, and nothing tells a reviewer that.

## Residual risks

1. **`dind` is privileged.** A real risk, scoped to the nested daemon, which holds
   only sandbox images and no secrets. Never run this compose file on a host you
   do not control. Production replaces dind with a dedicated runner fleet behind
   a socket proxy permitting only `create`/`start`/`exec`/`stop`/`remove`.
2. **gVisor is not a kernel boundary for the host.** It is a strong boundary for
   the *container*. A gVisor vulnerability is a platform vulnerability.
3. **Wall-clock enforcement is best-effort against a wedged daemon.** The task
   deadline is ours; the container's SIGKILL path goes through the daemon.
4. **A mutable image tag makes historical grades unreproducible.** Pin by digest
   in production; `image_digest` is recorded on every result.
5. **10.10 s of wall clock per task is a DoS vector.** Rate limiting and per-user
   concurrency caps are Phase 2 work.

## Verification status

| Layer | Runs on | Status |
| --- | --- | --- |
| Container-spec unit tests | any machine, no Docker | 88 passing |
| Payload isolation | any machine | included above |
| tmpfs exec + read-only rootfs | any Docker daemon | 6 passing |
| Adversarial suite under `runc` | Docker | Phase 1 step 6 |
| Adversarial suite under `runsc` | Docker + gVisor | Phase 1 step 6 (`-m gvisor`) |
| Load test to <2 s | Docker + gVisor | Phase 1 step 7 |

macOS cannot run gVisor, so the bottom two rows require a Linux runner. That is a
platform limitation, not a coverage gap being papered over.