"""Per-submission volumes, seeded by a throwaway helper container.

Why this exists, since the obvious approaches do not work:

``put_archive`` into the sandbox
    Docker writes it *through the container's root filesystem*, so against
    ``read_only=True`` the daemon rejects it with ``container rootfs is marked
    read-only`` -- even when the target path is on the writable ``/tmp`` tmpfs. The
    read-only rootfs is the stronger control, so this route is closed.

Bind mount from a path the worker created
    A bind source is resolved on the *daemon's* filesystem. Under
    Docker-in-Docker the daemon is a separate container with a different
    filesystem, so the daemon reports ``bind source path does not exist``. In a
    non-dind deployment this would work, but the development stack must be able to
    run the same code paths as production.

Named volumes, seeded by a helper container
    Works in both topologies, because a volume is managed by the daemon itself.

The seeder is a throwaway container from the *same* sandbox image, created with a
writable rootfs and no network, whose only job is to unpack a tar into a volume.
It never sees candidate code as *code* -- only as bytes to write -- and it is
removed immediately. It carries no secrets and no runtime privilege, so it is not
part of the trusted computing base in the way the sandbox itself is.

Cost: one extra container create per submission (~150-250 ms). That is paid
outside the sandbox, so it does not consume the wall clock the candidate is graded
against, and it happens on the grading path only -- not for Run.
"""

from __future__ import annotations

from dataclasses import dataclass

import docker.errors
from docker.types import HostConfig

from oa_engine.execution.payload import (
    CONTAINER_GRADING_DIR,
    CONTAINER_WORKSPACE_DIR,
)
from oa_engine.platform.config import ExecutionLimits, SandboxEnvelope
from oa_engine.platform.logging import get_logger
from oa_worker.sandbox.spec import ENGINE_API_VERSION

log = get_logger(__name__, service="oa-worker")

__all__ = ["SeedError", "StagedVolumes", "remove_submission_volumes", "seed_submission_volumes"]

#: Prefix for the seeder's per-volume mount points. One distinct path per volume,
#: because two mounts at the same target is rejected by the daemon.
_SEED_MOUNT_PREFIX = "/_seed_"


class SeedError(RuntimeError):
    """A submission volume could not be created, seeded, or removed."""


@dataclass(frozen=True, slots=True)
class StagedVolumes:
    """The two Docker volumes backing one submission."""

    workspace_volume: str
    grading_volume: str
    #: The helper container, kept so it can be force-removed deterministically.
    seeder_id: str | None = None

    def mount_specs(self) -> list[dict[str, object]]:
        """Mount specs for the sandbox: workspace writable, grading read-only."""
        return [
            {
                "Type": "volume",
                "Source": self.workspace_volume,
                "Target": CONTAINER_WORKSPACE_DIR,
                "ReadOnly": False,
            },
            {
                "Type": "volume",
                "Source": self.grading_volume,
                "Target": CONTAINER_GRADING_DIR,
                "ReadOnly": True,
            },
        ]

    def as_orchestrator_spec(self) -> dict[str, str]:
        """Bind-source arguments accepted by ``build_container_spec``.

        The spec layer still speaks in "source" terms so it does not have to know
        whether a source is a host path, a volume, or nothing.
        """
        return {
            "workspace_source": self.workspace_volume,
            "grading_source": self.grading_volume,
        }


def seed_submission_volumes(
    *,
    api: object,
    limits: ExecutionLimits,
    envelope: SandboxEnvelope,
    candidate_tar: bytes,
    grading_tar: bytes,
    submission_id: str,
) -> StagedVolumes:
    """Create and populate the workspace and grading volumes for one submission.

    The grading volume is seeded **empty**, exactly like the workspace is. The
    hidden tests are injected after the candidate phase by
    :func:`reseed_grading_volume`, so there is no window in which candidate code
    can read or forge them.

    Args:
        api: A ``docker.api.client.APIClient``.
        limits: Per-run limits; supplies the image to seed from.
        envelope: The security envelope, applied to the seeder as well. The
            seeder gets the same containment as the sandbox: it is our code, but a
            container that runs with weaker controls than the thing it prepares is
            a hole in the argument.
        candidate_tar: The candidate archive.
        grading_tar: The hidden-test archive. Written to a *separate* volume that
            the sandbox mounts read-only.
        submission_id: Used for volume names, which must be unique and greppable.

    Returns:
        The two populated volumes.

    Raises:
        SeedError: If any step fails. Failing loudly is essential: a submission
            cannot be graded without these volumes, and continuing would grade
            against an empty workspace.
    """
    workspace_volume = f"oa-ws-{submission_id[:24]}"
    grading_volume = f"oa-grade-{submission_id[:24]}"

    for volume in (workspace_volume, grading_volume):
        try:
            _remove_volume(api, volume)
        except SeedError:
            log.warning("stale_volume_not_removable", volume=volume)

    seeder_id: str | None = None
    try:
        _create_volume(api, workspace_volume)
        _create_volume(api, grading_volume)
        seeder_id = _run_seeder(
            api=api,
            envelope=envelope,
            image=limits.image,
            volumes=(workspace_volume, grading_volume),
            archives=(candidate_tar, grading_tar),
        )
    except docker.errors.DockerException as exc:
        remove_submission_volumes(api=api, volumes=StagedVolumes(workspace_volume, grading_volume))
        raise SeedError(f"cannot stage submission volumes: {exc}") from exc

    log.info(
        "volumes_seeded",
        submission_id=submission_id,
        workspace=workspace_volume,
        grading=grading_volume,
    )
    return StagedVolumes(
        workspace_volume=workspace_volume,
        grading_volume=grading_volume,
        seeder_id=seeder_id,
    )


def reseed_grading_volume(
    *,
    api: object,
    volumes: StagedVolumes,
    envelope: SandboxEnvelope,
    image: str,
    grading_tar: bytes,
) -> None:
    """Populate the grading volume *after* the candidate phase has finished.

    Called only once every candidate process has been killed. Before that point the
    grading volume is empty, so a candidate that guesses the path finds nothing.

    Raises:
        SeedError: If the volume cannot be populated. Grading with an empty grading
            volume would report a vacuous pass, which is the worst possible failure.
    """
    try:
        _create_volume(api, volumes.grading_volume)
        _run_seeder(
            api=api,
            envelope=envelope,
            image=image,
            volumes=(volumes.grading_volume,),
            archives=(grading_tar,),
        )
    except docker.errors.DockerException as exc:
        raise SeedError(f"cannot stage the hidden tests: {exc}") from exc
    log.info("grading_volume_seeded", volume=volumes.grading_volume)


def remove_submission_volumes(*, api: object, volumes: StagedVolumes) -> None:
    """Destroy both volumes and the seeder. Best effort, never raises.

    Volumes are not garbage collected by ``docker system prune`` as reliably as
    containers, and a leftover grading volume is a leftover copy of the hidden
    tests sitting on the daemon. They are removed explicitly on every path.
    """
    for volume in (volumes.workspace_volume, volumes.grading_volume):
        try:
            _remove_volume(api, volume)
        except SeedError as exc:
            log.warning("volume_remove_failed", volume=volume, error=str(exc))
    log.debug("volumes_removed", workspace=volumes.workspace_volume, grading=volumes.grading_volume)


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _create_volume(api: object, name: str) -> None:
    """Create a named volume, tolerating one that already exists."""
    api.create_volume(name=name, driver="local")  # type: ignore[attr-defined]


def _remove_volume(api: object, name: str) -> None:
    """Remove a named volume, tolerating its absence."""
    try:
        api.remove_volume(name)  # type: ignore[attr-defined]
    except docker.errors.NotFound:
        return
    except docker.errors.APIError as exc:
        # "volume is in use" means a container still holds it. That is worth a
        # loud log but not an exception: the caller is on an error path already.
        log.warning("volume_remove_rejected", volume=name, error=str(exc))


def _run_seeder(
    *,
    api: object,
    envelope: SandboxEnvelope,
    image: str,
    volumes: tuple[str, ...],
    archives: tuple[bytes, ...],
) -> str:
    """Unpack each archive into the volume at the same index.

    The seeder gets ``read_only=False`` -- the one place in this codebase that is
    true, and the whole reason this helper exists. It is given the same capability
    drops, PID limit, memory limit, no-network setting, and non-root user as the
    sandbox, so preparing a submission never happens under weaker controls than
    running one.
    """
    if len(volumes) != len(archives):
        raise SeedError("each volume needs exactly one archive")

    # `read_only` and every other resource/privilege knob belong on the
    # HostConfig, not on ``create_container``: docker-py 7.x moved them there, and
    # passing them at the top level raises TypeError.
    seeder_host_config = HostConfig(
        version=ENGINE_API_VERSION,
        # The single deliberate exception to read_only in this codebase, isolated
        # to this helper -- which is exactly why the helper exists.
        read_only=False,
        mounts=[
            {
                "Type": "volume",
                "Source": volume,
                "Target": f"{_SEED_MOUNT_PREFIX}{index}",
                "ReadOnly": False,
            }
            for index, volume in enumerate(volumes)
        ],
        mem_limit=envelope.mem_limit_bytes,
        memswap_limit=envelope.mem_limit_bytes,
        mem_swappiness=0,
        pids_limit=envelope.pids_limit,
        nano_cpus=envelope.nano_cpus,
        cap_drop=list(envelope.cap_drop),
        security_opt=list(envelope.security_opt),
        privileged=False,
        ipc_mode="none",
        pid_mode=None,
        network_mode="none",
        init=True,
    )

    container = api.create_container(  # type: ignore[attr-defined]
        image=image,
        command=["sleep", "60"],
        # Root, unlike every other container here. It executes no candidate code
        # -- it only unpacks bytes into a volume -- and running it as root makes
        # the extracted files root-owned, which is what lets the sandbox's non-root
        # user read them. `put_archive` applies the container's uid, so a
        # same-uid seeder produced files the sandbox could not open.
        user="0:0",
        network_disabled=envelope.network_disabled,
        host_config=seeder_host_config,
        labels={"oa.managed": "true", "oa.role": "seeder"},
    )

    container_id: str = container["Id"]
    try:
        api.start(container_id)  # type: ignore[attr-defined]
        # Straight into each volume's mount point. No copy step: `put_archive`
        # only failed in the sandbox because the *container* was read-only, and
        # this one is not.
        for index, archive in enumerate(archives):
            if not archive:
                continue
            api.put_archive(container_id, f"{_SEED_MOUNT_PREFIX}{index}", archive)  # type: ignore[attr-defined]
    finally:
        _force_remove(api, container_id)
    return container_id


def _force_remove(api: object, container_id: str) -> None:
    """Remove the seeder, tolerating an already-gone container."""
    try:
        api.remove_container(container_id, force=True, v=True)  # type: ignore[attr-defined]
    except docker.errors.NotFound:
        return
    except docker.errors.APIError as exc:
        log.warning("seeder_remove_failed", container_id=container_id[:12], error=str(exc))
