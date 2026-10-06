#!/usr/bin/env bash
# Build every sandbox image in the daemon that will run them.
#
# Tags are versioned rather than :latest because grading must be reproducible:
# a mutable tag means the same submission could be graded by different code a
# week apart, which quietly invalidates historical mastery estimates.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX="${SANDBOX_IMAGE_PREFIX:-oa}"
TAG="${SANDBOX_IMAGE_TAG:-1.0.0}"
REGISTRY="${SANDBOX_REGISTRY:-}"

log() { printf '[sandbox-images] %s\n' "$*" >&2; }
die() { printf '[sandbox-images] FATAL: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker CLI not available"

build_one() {
    local lang="$1" dockerfile="$2"
    local tag="${PREFIX}/sandbox-${lang}:${TAG}"
    local ref="${REGISTRY:+${REGISTRY%/}/}${tag}"

    log "building ${ref}"
    # --platform is pinned so a developer's arm64 Mac and an x86 CI runner
    # produce functionally identical graders.
    DOCKER_BUILDKIT=1 docker build \
        --platform linux/amd64 \
        --file "${ROOT}/sandbox/images/${dockerfile}" \
        --tag "${ref}" \
        --label "oa.sandbox.built-at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "${ROOT}"

    # Record the digest. This is what the worker pins at grading time so a
    # re-pull cannot change a grade retroactively.
    local digest
    digest="$(docker image inspect "${ref}" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
    if [[ -n "$digest" ]]; then
        log "${ref} -> ${digest}"
    else
        log "${ref} built (no registry digest; local-only image)"
    fi
    printf '%s\n' "${ref}" >> "${SANDBOX_IMAGE_MANIFEST:-/tmp/oa-sandbox-images.txt}"
}

MANIFEST="${SANDBOX_IMAGE_MANIFEST:-/tmp/oa-sandbox-images.txt}"
: > "$MANIFEST"

log "target platform linux/amd64, tag ${TAG}"
build_one python Dockerfile.python
build_one react  Dockerfile.react
build_one sql    Dockerfile.sql
build_one java   Dockerfile.java

log "done: $(wc -l < "$MANIFEST") images"
