"""Shared platform configuration.

This module is the single source of truth for two very different kinds of
configuration, and the distinction between them is a security boundary:

``Settings`` (operational)
    Tunable via environment variables: image tags, pool sizes, broker URLs,
    log levels. These affect throughput and cost, never containment.

``SandboxEnvelope`` (security)
    A frozen dataclass whose fields have **no environment-variable path at
    all**. Memory limits, PID caps, capability drops and the required runtime
    are compile-time constants. A misconfigured or compromised deployment
    cannot loosen containment by setting an environment variable -- the only
    way to relax a control is to edit code and ship it, which is reviewable.

Choice, stated once: *any control that cannot be weakened by configuration is
compiled in; any control that only affects throughput is configurable.*
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Language(str, enum.Enum):
    """Supported execution languages, each backed by a pre-baked image."""

    PYTHON = "python"
    REACT = "react"
    SQL = "sql"
    JAVA = "java"


# --------------------------------------------------------------------------
# Security envelope -- compiled in, never configurable
# --------------------------------------------------------------------------

#: Variables that live in the shared ``.env`` but are consumed by something other
#: than :class:`Settings`: docker compose interpolation, the Next.js build, or the
#: sandbox image builder. Listed explicitly rather than switched off wholesale,
#: because the whole point of validating configuration is to notice a typo, and a
#: blanket ignore would turn ``WORKER_CONCURRANCYY=8`` into silence.
NON_SETTINGS_ENV_VARS: frozenset[str] = frozenset(
    {
        # Compose interpolation only: published host ports.
        "API_PORT",
        "WEB_PORT",
        "POSTGRES_PORT",
        "REDIS_PORT",
        "DIND_PORT",
        # Consumed by docker-compose.yml to build the worker/frontend services.
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        # Inlined into the Next.js bundle at build time.
        "NEXT_PUBLIC_API_BASE_URL",
        "NEXT_PUBLIC_WS_BASE_URL",
        # Consumed by sandbox/images/build-all.sh and infra/gvisor/*.sh.
        "SANDBOX_IMAGE_PREFIX",
        "SANDBOX_IMAGE_TAG",
        "SANDBOX_REGISTRY",
        "RUNSC_VERSION",
        "RUNSC_SHA256",
        "SANDBOX_PROBE_IMAGE",
    }
)


#: Runtimes the platform recognises by name. Membership is what makes a value
#: *meaningful* rather than merely well-formed: an unrecognised name would still
#: be a syntactically valid runtime request, and the daemon would reject it at
#: ``create_container`` time -- mid-submission, as an opaque error, instead of at
#: boot with a message naming the valid values.
KNOWN_RUNTIMES: frozenset[str] = frozenset({"runc", "runsc"})

#: The runtime used when nothing is configured. Deliberately ``runc``, not
#: ``runsc``: this is the setting a developer's machine can actually satisfy.
#: gVisor cannot run on macOS or Windows -- Docker Desktop virtualises the kernel
#: and gVisor *is* a kernel -- so defaulting to it would leave the platform
#: unstartable for most contributors while providing no security benefit on the
#: machines that could never run it anyway.
DEFAULT_SANDBOX_RUNTIME: str = "runc"

#: Runtime providing gVisor's userspace kernel -- the second isolation layer.
GVISOR_RUNTIME: str = "runsc"

#: UID/GID the sandboxed process runs as. Must be non-root. These IDs must match
#: the non-root user created in every ``sandbox/images/Dockerfile.*``.
SANDBOX_UID: int = 65532
SANDBOX_GID: int = 65532

#: Ephemeral writable mount. ``docker.types.Mount`` cannot express an executable
#: tmpfs (it hardcodes noexec for ``TmpfsOptions``), so we build the mount as a
#: raw dict and hand it to the low-level API client instead. See
#: ``oa_worker.sandbox.orchestrator.build_container_spec``.
SANDBOX_TMP_PATH: str = "/tmp"


@dataclass(frozen=True, slots=True)
class SandboxEnvelope:
    """Immutable containment policy applied to every execution container.

    Every field here is asserted by ``sandbox/tests/test_security_envelope.py``
    and by a live adversarial test in ``sandbox/tests/test_adversarial.py``.
    Adding a field without covering it in those tests is a CI failure.
    """

    # -- identity ----------------------------------------------------------
    # ``runtime`` is intentionally absent. The container runtime is a platform
    # *selection* (``Settings.sandbox_runtime``), not a containment control, and
    # conflating the two is what previously made it impossible to run a single
    # submission locally. Every other control in this class is identical in both
    # runtime modes, which ``test_controls_are_identical_across_runtimes`` asserts
    # so the distinction cannot quietly grow.
    user: str = f"{SANDBOX_UID}:{SANDBOX_GID}"

    # -- filesystem --------------------------------------------------------
    read_only_rootfs: bool = True
    tmp_size_bytes: int = 64 * 1024 * 1024  # 64 MiB, memory-backed
    tmp_mode: int = 0o1777  # sticky world-writable, like real /tmp
    tmp_exec: bool = True

    # -- resources ---------------------------------------------------------
    mem_limit_bytes: int = 256 * 1024 * 1024  # 256 MiB hard limit
    pids_limit: int = 128  # fork-bomb defence
    nano_cpus: int = 500_000_000  # 0.5 CPU, half a core

    # -- privileges -------------------------------------------------------
    cap_drop: tuple[str, ...] = ("ALL",)
    security_opt: tuple[str, ...] = ("no-new-privileges",)
    network_disabled: bool = True
    privileged: bool = False
    #: Namespace and identity controls. ``private`` PID/IPC namespaces are
    #: applied by the orchestrator, not here, because they belong to the
    #: HostConfig rather than to the envelope's identity fields.
    #:
    #: There is deliberately no ``userns_mode`` field. The Engine API accepts only
    #: "host" or an explicit uid/gid mapping for that parameter, so "private" is not
    #: a value it can express -- an earlier draft carried it and the daemon
    #: rejected the request outright. Isolation of the user's identity comes from
    #: running as a non-root uid with all capabilities dropped, which is stronger
    #: than userns remapping anyway.
    #:

    # -- time and output caps ---------------------------------------------
    #: Hard wall clock. Enforced by the worker with a kill+remove, *not* by
    #: Docker's ``stop_timeout``, because a hostile process can ignore SIGKILL's
    #: grace period and we must not depend on cooperative shutdown.
    wall_clock_timeout_s: int = 30
    #: Per-stream output cap before truncation is recorded in the result.
    max_stream_bytes: int = 256 * 1024  # 256 KiB each for stdout and stderr
    #: Cap on the serialised result payload we read back out of the container.
    max_result_bytes: int = 8 * 1024 * 1024  # 8 MiB
    #: Cap on total candidate-authored bytes we will inject.
    max_submission_bytes: int = 512 * 1024  # 512 KiB
    #: Cap on the number of files in a submission archive.
    max_submission_files: int = 200

    def validate_invariants(self) -> None:
        """Assert internal consistency of the envelope.

        Called at orchestrator construction time so a bad edit fails at process
        start rather than silently degrading containment mid-run.
        """
        if self.network_disabled is not True:
            raise ValueError("network_disabled must never be False")
        if self.privileged is not False:
            raise ValueError("privileged must never be True")
        if self.read_only_rootfs is not True:
            raise ValueError("read_only_rootfs must never be False")
        if "ALL" not in self.cap_drop:
            raise ValueError("cap_drop must include ALL")
        if "no-new-privileges" not in self.security_opt:
            raise ValueError("no-new-privileges is mandatory")
        if str(SANDBOX_UID) not in self.user or str(SANDBOX_GID) not in self.user:
            raise ValueError("sandbox user must be the non-root uid:gid")
        if self.mem_limit_bytes <= 0 or self.pids_limit <= 0 or self.nano_cpus <= 0:
            raise ValueError("resource limits must be positive")
        if self.wall_clock_timeout_s <= 0:
            raise ValueError("wall clock timeout must be positive")
        if self.tmp_exec is not True:
            raise ValueError("the /tmp mount must remain executable")


#: The process-wide envelope. Immutable; import this, do not rebuild it.
DEFAULT_ENVELOPE: SandboxEnvelope = SandboxEnvelope()

# --------------------------------------------------------------------------
# Operational settings -- environment driven, affect throughput only
# --------------------------------------------------------------------------


DEFAULT_GEMINI_MODELS = "gemini-3.6-flash,gemini-3.5-flash,gemini-3.8-flash,gemini-3.5-flash-lite"


class Settings(BaseSettings):
    """Operational configuration, sourced from the environment or ``.env``.

    ``extra="forbid"`` is intentional: a typo'd variable should fail the process
    at boot, not silently fall back to a default and produce a mystery in prod.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        # "ignore", not "forbid".
        #
        # One ``.env`` serves docker compose, the Next.js build, the sandbox image
        # builder, and this application. "forbid" therefore fails at boot on a
        # perfectly valid file, because ``WEB_PORT`` and ``NEXT_PUBLIC_API_BASE_URL``
        # are not settings of ours. That is a real outage caused by a good hygiene
        # setting, which is worse than the typo it was meant to catch.
        #
        # The typo protection is preserved by :meth:`validate_environment`, which
        # runs at startup and raises for any variable that is neither a field of
        # this model nor listed in :data:`NON_SETTINGS_ENV_VARS`. Ignored at parse
        # time so the app can boot; reported loudly so a misspelling is still an
        # error rather than a silence.
        extra="ignore",
        frozen=True,
    )

    # -- runtime identity --------------------------------------------------
    environment: str = Field(default="local", pattern="^(local|ci|staging|prod)$")
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    log_json: bool = True

    # -- service coordinates ------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 - container-local bind; compose publishes the port
    api_port: int = 8000
    web_origin: str = "http://localhost:3000"

    # -- datastores --------------------------------------------------------
    postgres_dsn: str = "postgresql+asyncpg://oa:oa@postgres:5432/oa"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    #: Redis logical database holding submission payloads and execution event
    #: streams. Separate from the broker and the result backend so that a broker
    #: eviction policy cannot discard a payload or a live stream. Shared by the
    #: API (writer) and the worker (reader) -- which is exactly why it is a
    #: setting rather than a constant in each package.
    payload_db: int = 2

    # -- queue topology ----------------------------------------------------
    queue_name: str = "sandbox.execute"
    #: Concurrency of the execution pool. Each slot holds one container at a time.
    worker_concurrency: int = 8

    # -- sandbox runtime ----------------------------------------------------
    #: Container runtime for every execution. ``runc`` (default) is plain Linux
    #: container isolation; ``runsc`` adds gVisor's userspace kernel as a second
    #: layer and is required in production.
    #:
    #: This is deliberately configurable, and that is a security-relevant change
    #: rather than a convenience one, so the reasoning is recorded here and in
    #: docs/security/sandbox.md: it was previously compiled in as ``runsc``, which
    #: made the platform unstartable on any host without gVisor -- and gVisor
    #: cannot exist on macOS or Windows. Trading a *default* for an explicit
    #: production setting keeps the platform usable in development while leaving
    #: production guarded by ``assert_gvisor_is_second_layer`` plus the
    #: ``gvisor`` pytest marker.
    sandbox_runtime: str = DEFAULT_SANDBOX_RUNTIME

    # -- LLM question generation ---------------------------------------------
    #: Only the worker's offline generation job reads these. Never the API, never a
    #: sandbox: a key inside a container that runs hostile code is a leaked key.
    #: ``SecretStr`` so a stray ``print(settings)`` or log line shows ``**********``.
    gemini_api_key: SecretStr | None = None
    #: Comma-separated fallback chain, tried in order. Blank means the default chain
    #: (compose passes an unset variable through as an empty string).
    gemini_model: str = DEFAULT_GEMINI_MODELS

    @field_validator("gemini_model", mode="after")
    @classmethod
    def _default_blank_gemini_model(cls, value: str) -> str:
        return value.strip() or DEFAULT_GEMINI_MODELS

    # -- sandbox images ----------------------------------------------------
    image_python: str = "oa/sandbox-python:1.0.0"
    image_react: str = "oa/sandbox-react:1.0.0"
    image_sql: str = "oa/sandbox-sql:1.0.0"
    image_java: str = "oa/sandbox-java:1.0.0"
    #: Suffix appended to give each warm container a unique, greppable name.
    container_name_prefix: str = "oa-sbx"

    # -- warm pool ----------------------------------------------------------
    pool_size_per_language: int = 4
    pool_acquire_timeout_s: float = 5.0
    #: How long an idle container survives before being culled and replaced.
    pool_container_max_idle_s: int = 300
    #: Background replenishment cadence, seconds.
    pool_replenish_interval_s: int = 5

    # -- redis / realtime ---------------------------------------------------
    #: TTL on the stream of execution events consumed by the WebSocket layer.
    stream_ttl_s: int = 900

    @classmethod
    def validate_environment(cls, *, env_file: Path = Path(".env")) -> None:
        """Raise for any variable in our ``.env`` this platform cannot account for.

        Called once at application start. Recovers the guarantee that
        ``extra="forbid"`` would have provided, without breaking on the variables
        that legitimately belong to other processes sharing the same file.

        Reads only the keys declared in ``env_file`` -- not the whole process
        environment. Scanning ``os.environ`` would flag ``PATH``, ``HOME``,
        ``TERM`` and every other variable the shell owns, which is noise and
        would train an operator to ignore the error entirely.

        Args:
            env_file: The ``.env`` to audit. Defaults to the working directory's.

        Raises:
            ValueError: Naming every unrecognised key at once. One-at-a-time
                discovery turns a typo hunt into a sequence of restarts.
        """
        if not env_file.is_file():
            return

        known = {name.lower() for name in cls.model_fields}
        allowed = {name.lower() for name in NON_SETTINGS_ENV_VARS}

        declared: set[str] = set()
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            declared.add(line.split("=", 1)[0].strip())

        unexpected = sorted(
            key for key in declared if key.lower() not in known and key.lower() not in allowed
        )
        if unexpected:
            raise ValueError(
                f"{env_file} declares variables this platform does not recognise: "
                f"{unexpected}. Each must either be a Settings field or be listed in "
                f"NON_SETTINGS_ENV_VARS, because an unrecognised name is silently "
                f"ignored and then fails later as a mystery. If the variable is for "
                f"another process sharing this file, add it to NON_SETTINGS_ENV_VARS "
                f"in oa_engine.platform.config with a note about who consumes it."
            )

    def redis_db_url(self, db: int) -> str:
        """Return ``redis_url`` with its database number replaced.

        Args:
            db: The logical database to target.

        Returns:
            A URL pointing at ``db``. Falls back to the default host when the
            configured URL carries no path, so a hand-written ``redis://host``
            does not silently become ``redis://host/2``.
        """
        base = self.redis_url.rstrip("/")
        head, sep, _tail = base.rpartition("/")
        if not sep or not head:
            return f"redis://redis:{db}"
        return f"{head}/{db}"

    def payload_url(self) -> str:
        """Redis URL for the payload and event-stream database."""
        return self.redis_db_url(self.payload_db)

    def broker_url(self) -> str:
        """Effective Celery broker, falling back to :attr:`redis_url`."""
        return self.celery_broker_url or self.redis_url

    def result_backend(self) -> str:
        """Effective Celery result backend, falling back to :attr:`redis_url`."""
        return self.celery_result_backend or self.redis_url

    def image_for(self, language: Language) -> str:
        """Resolve the pre-baked image tag for ``language``."""
        images: dict[Language, str] = {
            Language.PYTHON: self.image_python,
            Language.REACT: self.image_react,
            Language.SQL: self.image_sql,
            Language.JAVA: self.image_java,
        }
        return images[language]

    @field_validator("worker_concurrency")
    @classmethod
    def _positive_concurrency(cls, v: int) -> int:
        if v < 1:
            raise ValueError("worker_concurrency must be >= 1")
        return v

    @field_validator("pool_size_per_language")
    @classmethod
    def _positive_pool(cls, v: int) -> int:
        if v < 0:
            raise ValueError("pool_size_per_language must be >= 0")
        return v

    @field_validator("sandbox_runtime")
    @classmethod
    def _known_runtime(cls, v: str) -> str:
        """Reject a runtime the platform has no opinion about.

        Checked against a name list rather than a regex alone, so a typo fails at
        boot with a message naming the valid values. A purely syntactic check would
        let ``SANDBOX_RUNTIME=runs`` through and fail later, mid-submission, as an
        opaque daemon error.
        """
        if v not in KNOWN_RUNTIMES:
            raise ValueError(f"sandbox_runtime must be one of {sorted(KNOWN_RUNTIMES)}, got {v!r}")
        return v

    def gvisor_enabled(self) -> bool:
        """Whether gVisor is the selected isolation layer."""
        return self.sandbox_runtime == GVISOR_RUNTIME


def _name_fragment(submission_id: str) -> str:
    """Build a short, collision-resistant fragment of a submission id for a name.

    Keeps the first few characters of the id for human recognition and appends a
    random suffix for uniqueness. Docker names allow ``[a-zA-Z0-9][a-zA-Z0-9_.-]``
    and cap at 255, so a UUID fragment is safe without sanitising -- but an
    id from a caller that is *not* a UUID is stripped anyway, because a name that
    the daemon rejects is a 500 on an otherwise valid submission.
    """
    safe = "".join(char if char.isalnum() else "-" for char in submission_id)[:10].strip("-")
    return f"{safe or 'sub'}-{uuid4().hex[:6]}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings`.

    Cached because ``BaseSettings`` reads the filesystem and environment on
    every construction, and per-request construction would be both slow and a
    source of confusing mid-request behaviour. Tests should call
    ``get_settings.cache_clear()`` after mutating the environment.
    """
    return Settings()


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    """Derived, per-language view of the envelope for a single run."""

    language: Language
    image: str
    runtime: str
    timeout_s: int
    max_stream_bytes: int
    max_result_bytes: int
    container_name: str
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        language: Language,
        settings: Settings,
        envelope: SandboxEnvelope,
        submission_id: str,
        *,
        image_digest: str | None = None,
        runtime: str | None = None,
    ) -> ExecutionLimits:
        """Compose the limits for one submission.

        Args:
            language: Which sandbox image to use.
            settings: Operational configuration.
            envelope: The immutable security envelope.
            submission_id: Drives a deterministic container name so a redelivered
                Celery task addresses the same container instead of creating a
                second one.
            image_digest: When supplied, pins the run to ``tag@sha256:...``. A
                mutable tag is fine in development but makes a grade
                unreproducible, because the hidden tests could change under a
                re-pulled tag. Production should always pin.
            runtime: Container runtime to request. Defaults to the configured
                ``Settings.sandbox_runtime``.

        Labels carry no user-controlled content; they exist so an operator can
        find a specific submission's container with ``docker ps --filter``.
        """
        base_image = settings.image_for(language)
        image = f"{base_image}@{image_digest}" if image_digest else base_image
        return cls(
            language=language,
            image=image,
            runtime=runtime or settings.sandbox_runtime,
            timeout_s=envelope.wall_clock_timeout_s,
            max_stream_bytes=envelope.max_stream_bytes,
            max_result_bytes=envelope.max_result_bytes,
            # Truncated *and* suffixed with a random component. Truncation alone is
            # a collision bug: the first 12 characters of two submissions that share
            # a prefix are identical, and for a pooled container the input is a
            # constant like "pool-python-", so every container would take the same
            # name and `create_container` would 409. A short random suffix makes
            # the name unique while staying greppable by language.
            container_name=(
                f"{settings.container_name_prefix}-{language.value}-{_name_fragment(submission_id)}"
            ),
            labels={
                "oa.managed": "true",
                "oa.language": language.value,
                "oa.submission": submission_id[:64],
                "oa.runtime": runtime or settings.sandbox_runtime,
            },
        )


# Fail fast at import time in every process that imports this module. A
# compromised control surface is a boot failure, not a 3am incident.
DEFAULT_ENVELOPE.validate_invariants()
