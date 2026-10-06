#!/usr/bin/env sh
# Idempotent gVisor status check and registration for the dind daemon.
#
# Scope, stated precisely because the failure mode here is subtle: **building**
# sandbox images does not require gVisor, **running** a sandbox does. This script
# therefore reports gVisor's absence and exits 0 by default, letting image builds
# proceed on a host that cannot execute sandboxes (notably macOS, where Docker
# Desktop virtualises the kernel and runsc cannot exist).
#
# The fail-closed guarantee lives in exactly two places, neither of which is this
# script:
#   * `infra/gvisor/install-runsc.sh` exits non-zero if installation fails.
#   * `SandboxOrchestrator.__init__` refuses to start when `runsc` is absent from
#     the daemon's runtime list, so no sandbox can ever run unprotected.
#
# Set REGISTER_REQUIRED=true on a Linux host to turn absence into a hard failure,
# which is what production and the adversarial CI job do.
set -eu

REQUIRED="${REGISTER_REQUIRED:-false}"
PROBE_IMAGE="${SANDBOX_PROBE_IMAGE:-busybox:1.36}"

have_runsc() { command -v runsc >/dev/null 2>&1; }

registered() {
    docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"runsc"'
}

if ! registered; then
    echo "[runsc] runsc is not registered on this daemon" >&2
    if have_runsc; then
        echo "[runsc] a runsc binary exists at $(command -v runsc); register it with:" >&2
        echo "[runsc]   ./infra/gvisor/install-runsc.sh" >&2
    else
        echo "[runsc] no runsc binary is present on this host. Sandbox IMAGES can" >&2
        echo "[runsc] still be built; sandbox EXECUTION cannot. Install with:" >&2
        echo "[runsc]   ./infra/gvisor/install-runsc.sh" >&2
        echo "[runsc] macOS and Windows cannot run gVisor at all. See" >&2
        echo "[runsc] infra/gvisor/README.md for the supported alternatives." >&2
    fi

    if [ "$REQUIRED" = "true" ]; then
        echo "[runsc] FATAL: REGISTER_REQUIRED=true and runsc is unavailable." >&2
        exit 1
    fi
    echo "[runsc] continuing without gVisor; the orchestrator will refuse to run sandboxes" >&2
    exit 0
fi

echo "[runsc] runsc is registered" >&2

# Registered is not the same as working. A daemon can advertise a runtime and
# still fail to start a container with it, and that failure would otherwise only
# surface as a mysterious infra error on a candidate's first submission.
echo "[runsc] smoke-testing a runsc container" >&2
if ! docker run --rm --runtime=runsc --network=none --read-only --entrypoint /bin/true \
        "$PROBE_IMAGE" >/dev/null 2>&1; then
    echo "[runsc] FATAL: the daemon advertises runsc but cannot start a container with it." >&2
    echo "[runsc] Check that the daemon reaches /var/lib/docker and that its" >&2
    echo "[runsc] default-runtime is runsc in /etc/docker/daemon.json." >&2
    exit 1
fi

echo "[runsc] runsc verified working" >&2
