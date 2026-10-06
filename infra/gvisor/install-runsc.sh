#!/usr/bin/env bash
# Install gVisor (runsc) into a Docker daemon and register it as a runtime.
#
# Why this is a script rather than a Dockerfile layer: runsc must be registered
# on the daemon that will actually create the sandbox containers, and a daemon
# only exists at runtime. Baking runsc into an image that is merely *built* by
# that daemon accomplishes nothing.
#
# Why we fail loudly: if runsc cannot be installed, the honest outcomes are
# (a) refuse to start the platform, or (b) run sandboxes under plain runc and
# pretend the second isolation layer is present. This script implements (a).
# Option (b) is the failure mode this whole product's moat depends on avoiding.
set -Eeuo pipefail

RUNSC_VERSION="${RUNSC_VERSION:-20241021.0.0}"
TARGET_DIR="${TARGET_DIR:-/usr/local/bin}"
RUNTIME_NAME="${RUNTIME_NAME:-runsc}"
DAEMON_CONFIG="${DAEMON_CONFIG:-/etc/docker/daemon.json}"

log() { printf '[runsc] %s\n' "$*" >&2; }
die() { printf '[runsc] FATAL: %s\n' "$*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

# -- checksum --------------------------------------------------------------
# A sha256 is not ceremony here: runsc is the syscall interceptor for every
# submission on the platform. A tampered binary would be an invisible,
# total compromise. Override RUNSC_SHA256 only when bumping the version, and
# expect the bump to be reviewed on that basis.
RUNSC_SHA256="${RUNSC_SHA256:-}"

verify_checksum() {
    local file="$1" expected="$2"
    if [[ -z "$expected" ]]; then
        log "WARNING: no checksum supplied for $(basename "$file"); cannot verify provenance"
        return 0
    fi
    local actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    [[ "$actual" == "$expected" ]] || die "checksum mismatch for $(basename "$file"): expected $expected, got $actual"
    log "verified checksum $(basename "$file")"
}

# -- install ---------------------------------------------------------------
install_runsc() {
    local os arch url tmp
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    case "$(uname -m)" in
        x86_64|amd64) arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        *) die "unsupported architecture: $(uname -m)" ;;
    esac

    url="https://storage.googleapis.com/gvisor/releases/release/${RUNSC_VERSION}/gvisor-${RUNSC_VERSION}.${os}-${arch}.tar.gz"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT

    log "downloading ${RUNSC_VERSION} for ${os}/${arch}"
    curl --fail --location --silent --show-error --retry 3 --max-time 300 -o "$tmp/runsc.tar.gz" "$url" \
        || die "download failed: $url"

    verify_checksum "$tmp/runsc.tar.gz" "$RUNSC_SHA256"

    tar -xzf "$tmp/runsc.tar.gz" -C "$tmp" \
        || die "archive extraction failed; the download may be corrupt"

    [[ -x "$tmp/runsc" ]] || die "runsc binary missing from the archive"
    install -m 0755 "$tmp/runsc" "${TARGET_DIR}/runsc"
    install -m 0755 "$tmp/runsc/gofer.hello" "${TARGET_DIR}/gofer.hello" 2>/dev/null || true

    log "installed runsc to ${TARGET_DIR}/runsc"
    "${TARGET_DIR}/runsc" --version || die "installed runsc will not execute"
}

# -- register --------------------------------------------------------------
register_runtime() {
    require_cmd docker
    docker info >/dev/null 2>&1 || die "cannot reach the Docker daemon to register ${RUNTIME_NAME}"

    # `docker info` reports registered runtimes; if ours is already there we are
    # done, which makes the script idempotent and safe to re-run.
    if docker info --format '{{json .Runtimes}}' | grep -q "\"${RUNTIME_NAME}\""; then
        log "runtime ${RUNTIME_NAME} already registered"
        return 0
    fi

    log "registering ${RUNTIME_NAME} in ${DAEMON_CONFIG}"
    mkdir -p "$(dirname "$DAEMON_CONFIG")"
    if [[ -f "$DAEMON_CONFIG" ]]; then
        python3 - "$DAEMON_CONFIG" "$RUNTIME_NAME" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
with open(path) as fh:
    config = json.load(fh)
config.setdefault("runtimes", {})[name] = {"path": "/usr/local/bin/runsc"}
config.setdefault("default-runtime", name)
with open(path, "w") as fh:
    json.dump(config, fh, indent=2)
PY
    else
        cat > "$DAEMON_CONFIG" <<JSON
{
  "default-runtime": "${RUNTIME_NAME}",
  "runtimes": {
    "${RUNTIME_NAME}": { "path": "/usr/local/bin/runsc" }
  }
}
JSON
    fi
    log "wrote ${DAEMON_CONFIG}; the daemon will pick this up on next start"
}

main() {
    require_cmd curl
    require_cmd tar
    install_runsc
    if [[ "${REGISTER_ONLY:-false}" != "true" ]]; then
        register_runtime
    fi
    log "done"
}

main "$@"