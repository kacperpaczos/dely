"""The run configuration.

One document names the backend, the pinned revisions and versions, the single
task and the single check, where artifacts land, and how the environment
authenticates. It never names a credential value: a key whose name means
"secret" is refused, and so is a value that redaction would change.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import redact
from .admission import Budget, Claim, Limits, SEQUENTIAL_MAX_ACTIVE

try:  # pragma: no cover - environment dependent
    import yaml as _DEFAULT_YAML
except ImportError:  # pragma: no cover - environment dependent
    _DEFAULT_YAML = None

BACKENDS = ("distrobox", "vm")
AUTH_MODES = ("existing_login", "short_lived_token", "api_key_helper")

#: Keys whose name mentions a secret but whose value is the *name* of
#: something rather than the thing itself.
NAME_ONLY_KEYS = frozenset({"token_env"})

_UNSET = object()


class ConfigError(ValueError):
    """The configuration document cannot be used as written."""


def _fail(message: str) -> None:
    raise ConfigError(message)


def _section(document: Mapping[str, Any], name: str, *, required: bool) -> dict:
    value = document.get(name)
    if value is None:
        if required:
            _fail(f"missing required section: {name}")
        return {}
    if not isinstance(value, Mapping):
        _fail(f"section {name} must be a mapping")
    return dict(value)


def _reject_unknown(document: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    for key in document:
        if key not in allowed:
            _fail(f"unknown key in {where}: {key}")


def _text(document: Mapping[str, Any], key: str, where: str, default=_UNSET) -> str:
    if key not in document:
        if default is _UNSET:
            _fail(f"missing required field: {where}.{key}" if where else f"missing required field: {key}")
        return default
    value = document[key]
    if not isinstance(value, str) or not value.strip():
        _fail(f"field {where}.{key} must be a non-empty string" if where else f"field {key} must be a non-empty string")
    return value


def _flag(document: Mapping[str, Any], key: str, where: str, default: bool) -> bool:
    if key not in document:
        return default
    value = document[key]
    if not isinstance(value, bool):
        _fail(f"field {where}.{key} must be true or false, got {value!r}")
    return value


def _positive_int(document: Mapping[str, Any], key: str, where: str, default=_UNSET) -> int:
    if key not in document:
        if default is _UNSET:
            _fail(f"missing required field: {key}")
        return default
    value = document[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(f"field {key} must be a positive whole number, got {value!r}")
    return value


def _absolute(document: Mapping[str, Any], key: str) -> Path:
    raw = _text(document, key, "")
    path = Path(raw)
    if not path.is_absolute():
        _fail(f"field {key} must be an absolute path, got {raw!r}")
    return path


def _relative(value: str, where: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        _fail(f"{where} must be a relative path that stays inside its root, got {value!r}")
    return candidate.as_posix()


def _argv(document: Mapping[str, Any], key: str, where: str, default=_UNSET) -> tuple[str, ...]:
    if key not in document:
        if default is _UNSET:
            _fail(f"missing required field: {where}.{key}")
        return tuple(default)
    value = document[key]
    if not isinstance(value, (list, tuple)) or not value:
        _fail(f"field {where}.{key} must be a non-empty list of strings")
    for item in value:
        if not isinstance(item, str):
            _fail(f"field {where}.{key} must contain only strings")
    return tuple(value)


def refuse_secrets(document: Any, trail: str = "") -> None:
    """Refuse a document that names a secret or carries a secret-shaped value."""
    if isinstance(document, Mapping):
        for key, value in document.items():
            here = f"{trail}.{key}" if trail else str(key)
            if (
                isinstance(key, str)
                and key not in NAME_ONLY_KEYS
                and redact.SENSITIVE_KEY.match(key)
            ):
                _fail(f"configuration key names a secret and must not exist: {here}")
            refuse_secrets(value, here)
    elif isinstance(document, (list, tuple)):
        for index, item in enumerate(document):
            refuse_secrets(item, f"{trail}[{index}]")
    elif isinstance(document, str):
        if not redact.looks_secret_free(document):
            _fail(f"configuration value looks like a secret at {trail or 'the document root'}")


@dataclass(frozen=True)
class ProjectConfig:
    source: Path
    revision: str
    environment_path: str = "project"

    def to_document(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "revision": self.revision,
            "environment_path": self.environment_path,
        }


@dataclass(frozen=True)
class TaskConfig:
    marker: str
    relative_path: str
    prompt_path: str | None = None

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "marker": self.marker,
            "relative_path": self.relative_path,
        }
        if self.prompt_path is not None:
            document["prompt_path"] = self.prompt_path
        return document


@dataclass(frozen=True)
class CheckConfig:
    relative_path: str
    expected_marker: str

    def to_document(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "expected_marker": self.expected_marker,
        }


@dataclass(frozen=True)
class AuthConfig:
    mode: str
    reference: str
    allowlist: tuple[str, ...] = ()
    token_env: str | None = None
    helper_argv: tuple[str, ...] = ()

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"mode": self.mode, "reference": self.reference}
        if self.allowlist:
            document["allowlist"] = list(self.allowlist)
        if self.token_env is not None:
            document["token_env"] = self.token_env
        if self.helper_argv:
            document["helper_argv"] = list(self.helper_argv)
        return document


@dataclass(frozen=True)
class OrcaConfig:
    version: str
    model: str
    effort: str
    agent: str = "claude"
    status_argv: tuple[str, ...] = ("orca", "status", "--json")
    version_argv: tuple[str, ...] = ("orca", "--version")
    run_objective: str = "dely minimal cycle"
    worktree_selector: str = "current"
    ready_timeout_seconds: int = 300
    app_argv: tuple[str, ...] = ("/opt/Orca/orca-ide",)
    display: str = ":0"
    command: str = "orca"

    def to_document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "model": self.model,
            "effort": self.effort,
            "agent": self.agent,
            "status_argv": list(self.status_argv),
            "version_argv": list(self.version_argv),
            "run_objective": self.run_objective,
            "worktree_selector": self.worktree_selector,
            "ready_timeout_seconds": self.ready_timeout_seconds,
            "app_argv": list(self.app_argv),
            "display": self.display,
            "command": self.command,
        }


#: Where a skill's directory may sit under the environment's home. The first
#: is the shared directory the skills CLI installs into; the second is Claude
#: Code's own personal skills directory.
DEFAULT_SKILL_ROOTS = (".agents/skills", ".claude/skills")

#: Which screen an environment's application is pointed at.
GUI_MODES = ("virtual", "host")

#: What processor a guest is given. `default` leaves it to the emulator.
CPU_MODES = ("host-passthrough", "host-model", "default")


@dataclass(frozen=True)
class PinnedSkill:
    """One skill directory, pinned by the digest of its SKILL.md."""

    name: str
    sha256: str

    def to_document(self) -> dict[str, Any]:
        return {"name": self.name, "sha256": self.sha256}


@dataclass(frozen=True)
class PinnedPlugin:
    """One plugin checkout, pinned by the commit the environment must be at."""

    name: str
    path: str
    revision: str
    repository: str = ""
    version: str = ""
    marketplace: str = ""

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "revision": self.revision,
            "repository": self.repository,
            "version": self.version,
            "marketplace": self.marketplace,
        }


@dataclass(frozen=True)
class ReviewConfig:
    """Whether a second agent reviews the first one's work, and how long it gets."""

    enabled: bool = True

    def to_document(self) -> dict[str, Any]:
        return {"enabled": self.enabled}


@dataclass(frozen=True)
class SkillsConfig:
    """What the agent in the environment must be able to reach, and at what version."""

    required: bool = True
    roots: tuple[str, ...] = DEFAULT_SKILL_ROOTS
    bundled: tuple[PinnedSkill, ...] = ()
    plugins: tuple[PinnedPlugin, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "roots": list(self.roots),
            "bundled": [item.to_document() for item in self.bundled],
            "plugins": [item.to_document() for item in self.plugins],
        }

    @property
    def empty(self) -> bool:
        return not self.bundled and not self.plugins


@dataclass(frozen=True)
class DistroboxConfig:
    image: str
    container_prefix: str
    extra_mounts: tuple[str, ...] = ()
    provision: tuple[tuple[str, ...], ...] = ()
    accept_host_home_mount: bool = False
    # Which screen this box's application uses. `virtual` is its own; `host`
    # asks for windows on the operator's desktop and is never the default.
    gui: str = "virtual"
    # The share of the host one box may take. These are declared to the
    # container manager, so the budget admission checks is the same number the
    # kernel enforces, not a hope written in a manifest.
    vcpus: int = 2
    memory_mb: int = 4096
    pids: int = 2048
    disk_bytes: int = 8 * 1024 * 1024 * 1024

    def to_document(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "container_prefix": self.container_prefix,
            "extra_mounts": list(self.extra_mounts),
            "provision": [list(argv) for argv in self.provision],
            "accept_host_home_mount": self.accept_host_home_mount,
            "gui": self.gui,
            "vcpus": self.vcpus,
            "memory_mb": self.memory_mb,
            "pids": self.pids,
            "disk_bytes": self.disk_bytes,
        }

    @property
    def container_limit_flags(self) -> tuple[str, ...]:
        """The limits as the container manager takes them."""
        return (
            f"--cpus={self.vcpus}",
            f"--memory={self.memory_mb}m",
            f"--pids-limit={self.pids}",
        )


@dataclass(frozen=True)
class VmConfig:
    provider: str
    provider_version: str
    stack_prefix: str
    base_image: Path
    guest_user: str
    venv: Path
    # Empty means "whatever the build that produced this image recorded beside
    # it". A packer build is not reproducible byte for byte, so a digest
    # committed here would be wrong after every rebuild; the metadata the build
    # writes is what says this image is still the one it produced.
    base_image_sha256: str = ""
    pulumi_binary: str = "pulumi"
    connect_uri: str = "qemu:///system"
    pool: str = "dely-cycle"
    overlay_size_bytes: int = 24 * 1024 * 1024 * 1024
    memory_mb: int = 4096
    vcpus: int = 2
    graphics: str = "vnc"
    video: str = "virtio"
    listen_address: str = "127.0.0.1"
    transport: str = "ssh"
    network: str = "default"
    ssh_port: int = 22
    address_timeout_seconds: int = 600
    egress: bool = True
    # The processor the guest is given. `host-passthrough` is this machine's
    # own; `default` leaves the emulator to choose a conservative model of its
    # own, which is what it does when nothing says otherwise.
    cpu_mode: str = "host-passthrough"
    qemu_agent: bool = False
    provision: tuple[tuple[str, ...], ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_version": self.provider_version,
            "stack_prefix": self.stack_prefix,
            "base_image": str(self.base_image),
            "base_image_sha256": self.base_image_sha256,
            "guest_user": self.guest_user,
            "venv": str(self.venv),
            "pulumi_binary": self.pulumi_binary,
            "connect_uri": self.connect_uri,
            "pool": self.pool,
            "overlay_size_bytes": self.overlay_size_bytes,
            "memory_mb": self.memory_mb,
            "vcpus": self.vcpus,
            "graphics": self.graphics,
            "video": self.video,
            "listen_address": self.listen_address,
            "transport": self.transport,
            "network": self.network,
            "ssh_port": self.ssh_port,
            "address_timeout_seconds": self.address_timeout_seconds,
            "egress": self.egress,
            "cpu_mode": self.cpu_mode,
            "qemu_agent": self.qemu_agent,
            "provision": [list(argv) for argv in self.provision],
        }

    @property
    def base_volume_name(self) -> str:
        """The base image's name as the storage pool knows it."""
        return self.base_image.name


@dataclass(frozen=True)
class RunConfig:
    backend: str
    tested_revision: str
    dely_revision: str
    claude_code_version: str
    timeout_seconds: int
    artifact_root: Path
    state_root: Path
    project: ProjectConfig
    task: TaskConfig
    check: CheckConfig
    auth: AuthConfig
    orca: OrcaConfig
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    limits: Limits = field(default_factory=Limits)
    distrobox: DistroboxConfig | None = None
    vm: VmConfig | None = None
    source_path: Path | None = field(default=None, compare=False)

    def claim(self) -> Claim:
        """What this run asks the host for, in the dimensions admission counts."""
        section = self.distrobox if self.backend == "distrobox" else self.vm
        if section is None:
            return Claim(timeout_seconds=self.timeout_seconds)
        return Claim(
            vcpus=section.vcpus,
            memory_mb=section.memory_mb,
            pids=getattr(section, "pids", 0),
            disk_bytes=getattr(section, "disk_bytes", 0)
            or getattr(section, "overlay_size_bytes", 0),
            timeout_seconds=self.timeout_seconds,
        )

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "backend": self.backend,
            "tested_revision": self.tested_revision,
            "dely_revision": self.dely_revision,
            "claude_code_version": self.claude_code_version,
            "timeout_seconds": self.timeout_seconds,
            "artifact_root": str(self.artifact_root),
            "state_root": str(self.state_root),
            "project": self.project.to_document(),
            "task": self.task.to_document(),
            "check": self.check.to_document(),
            "auth": self.auth.to_document(),
            "orca": self.orca.to_document(),
            "skills": self.skills.to_document(),
            "review": self.review.to_document(),
            "limits": self.limits.to_document(),
        }
        if self.distrobox is not None:
            document["distrobox"] = self.distrobox.to_document()
        if self.vm is not None:
            document["vm"] = self.vm.to_document()
        return document

    def with_source(self, path: Path) -> "RunConfig":
        return replace(self, source_path=path)


_TOP_LEVEL = (
    "backend",
    "tested_revision",
    "dely_revision",
    "claude_code_version",
    "timeout_seconds",
    "artifact_root",
    "state_root",
    "project",
    "task",
    "check",
    "auth",
    "orca",
    "skills",
    "review",
    "limits",
    "distrobox",
    "vm",
)


def _project(document: Mapping[str, Any]) -> ProjectConfig:
    _reject_unknown(document, ("source", "revision", "environment_path"), "project")
    source = Path(_text(document, "source", "project"))
    if not source.is_absolute():
        _fail("field project.source must be an absolute path")
    return ProjectConfig(
        source=source,
        revision=_text(document, "revision", "project"),
        environment_path=_relative(
            _text(document, "environment_path", "project", "project"),
            "project.environment_path",
        ),
    )


def _task(document: Mapping[str, Any]) -> TaskConfig:
    _reject_unknown(document, ("marker", "relative_path", "prompt_path"), "task")
    return TaskConfig(
        marker=_text(document, "marker", "task"),
        relative_path=_relative(
            _text(document, "relative_path", "task"), "task.relative_path"
        ),
        prompt_path=document.get("prompt_path"),
    )


def _check(document: Mapping[str, Any], task: TaskConfig) -> CheckConfig:
    _reject_unknown(document, ("relative_path", "expected_marker"), "check")
    return CheckConfig(
        relative_path=_relative(
            _text(document, "relative_path", "check"), "check.relative_path"
        ),
        expected_marker=_text(document, "expected_marker", "check", task.marker),
    )


def _auth(document: Mapping[str, Any]) -> AuthConfig:
    _reject_unknown(
        document, ("mode", "reference", "allowlist", "token_env", "helper_argv"), "auth"
    )
    mode = _text(document, "mode", "auth")
    if mode not in AUTH_MODES:
        _fail(f"auth.mode must be one of {', '.join(AUTH_MODES)}, got {mode!r}")
    allowlist = tuple(document.get("allowlist") or ())
    for entry in allowlist:
        if not isinstance(entry, str):
            _fail("auth.allowlist must contain only strings")
        _relative(entry, "auth.allowlist entry")
    token_env = document.get("token_env")
    helper_argv = tuple(document.get("helper_argv") or ())
    if mode == "existing_login":
        if not allowlist:
            _fail("auth.allowlist must name at least one relative path for existing_login")
    elif allowlist:
        _fail(f"auth.allowlist is only meaningful for existing_login, not {mode}")
    if mode == "short_lived_token" and not token_env:
        _fail("auth.token_env must name the variable carrying the short-lived token")
    if mode == "api_key_helper" and not helper_argv:
        _fail("auth.helper_argv must name the helper command")
    return AuthConfig(
        mode=mode,
        reference=_text(document, "reference", "auth"),
        allowlist=allowlist,
        token_env=token_env,
        helper_argv=helper_argv,
    )


def _orca(document: Mapping[str, Any]) -> OrcaConfig:
    _reject_unknown(
        document,
        (
            "version",
            "model",
            "effort",
            "agent",
            "status_argv",
            "version_argv",
            "run_objective",
            "worktree_selector",
            "ready_timeout_seconds",
            "app_argv",
            "display",
            "command",
        ),
        "orca",
    )
    return OrcaConfig(
        version=_text(document, "version", "orca"),
        model=_text(document, "model", "orca"),
        effort=_text(document, "effort", "orca"),
        agent=_text(document, "agent", "orca", "claude"),
        status_argv=_argv(document, "status_argv", "orca", ("orca", "status", "--json")),
        version_argv=_argv(document, "version_argv", "orca", ("orca", "--version")),
        run_objective=_text(document, "run_objective", "orca", "dely minimal cycle"),
        worktree_selector=_text(document, "worktree_selector", "orca", "current"),
        ready_timeout_seconds=_positive_int(document, "ready_timeout_seconds", "orca", 300),
        app_argv=_argv(document, "app_argv", "orca", ("/opt/Orca/orca-ide",)),
        display=_text(document, "display", "orca", ":0"),
        command=_text(document, "command", "orca", "orca"),
    )


def _provision(document: Mapping[str, Any], where: str) -> tuple[tuple[str, ...], ...]:
    raw = document.get("provision") or ()
    if not isinstance(raw, (list, tuple)):
        _fail(f"field {where}.provision must be a list of argv lists")
    steps = []
    for index, argv in enumerate(raw):
        if not isinstance(argv, (list, tuple)) or not argv:
            _fail(f"field {where}.provision[{index}] must be a non-empty argv list")
        for item in argv:
            if not isinstance(item, str):
                _fail(f"field {where}.provision[{index}] must contain only strings")
        steps.append(tuple(argv))
    return tuple(steps)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _review(document: Mapping[str, Any]) -> ReviewConfig:
    if not document:
        return ReviewConfig()
    _reject_unknown(document, ("enabled",), "review")
    return ReviewConfig(enabled=_flag(document, "enabled", "review", True))


def _skills(document: Mapping[str, Any]) -> SkillsConfig:
    if not document:
        return SkillsConfig()
    _reject_unknown(document, ("required", "roots", "bundled", "plugins"), "skills")
    roots = tuple(document.get("roots") or DEFAULT_SKILL_ROOTS)
    for root in roots:
        if not isinstance(root, str) or not root.strip():
            _fail("skills.roots must contain only non-empty strings")
        if Path(root).is_absolute():
            _fail(
                f"skills.roots entry {root!r} is absolute; a root is relative to "
                "the environment's home, not to this host"
            )
    bundled = []
    for index, entry in enumerate(document.get("bundled") or ()):
        where = f"skills.bundled[{index}]"
        if not isinstance(entry, Mapping):
            _fail(f"{where} must be a mapping")
        _reject_unknown(entry, ("name", "sha256"), where)
        name = _text(entry, "name", where)
        sha256 = _text(entry, "sha256", where)
        if not _SHA256.match(sha256):
            _fail(
                f"{where}.sha256 is not a sha256 digest; a skill nothing pins is a "
                "skill that can be fetched from anywhere"
            )
        bundled.append(PinnedSkill(name=name, sha256=sha256))
    plugins = []
    for index, entry in enumerate(document.get("plugins") or ()):
        where = f"skills.plugins[{index}]"
        if not isinstance(entry, Mapping):
            _fail(f"{where} must be a mapping")
        _reject_unknown(
            entry,
            ("name", "path", "revision", "repository", "version", "marketplace"),
            where,
        )
        revision = _text(entry, "revision", where)
        if not _COMMIT.match(revision):
            _fail(
                f"{where}.revision is not a full commit; a tag or a branch is not "
                "a pin, because it moves"
            )
        path_value = _text(entry, "path", where)
        if not Path(path_value).is_absolute():
            _fail(f"{where}.path must be absolute inside the environment")
        plugins.append(
            PinnedPlugin(
                name=_text(entry, "name", where),
                path=path_value,
                revision=revision,
                repository=_text(entry, "repository", where, ""),
                version=_text(entry, "version", where, ""),
                marketplace=_text(entry, "marketplace", where, ""),
            )
        )
    return SkillsConfig(
        required=_flag(document, "required", "skills", True),
        roots=roots,
        bundled=tuple(bundled),
        plugins=tuple(plugins),
    )


def _budget(document: Mapping[str, Any]) -> Budget:
    _reject_unknown(
        document,
        ("vcpus", "memory_mb", "pids", "disk_bytes", "timeout_seconds"),
        "limits.budget",
    )
    return Budget(
        vcpus=_positive_int(document, "vcpus", "limits.budget"),
        memory_mb=_positive_int(document, "memory_mb", "limits.budget"),
        pids=_positive_int(document, "pids", "limits.budget"),
        disk_bytes=_positive_int(document, "disk_bytes", "limits.budget"),
        timeout_seconds=_positive_int(document, "timeout_seconds", "limits.budget"),
    )


def _limits(document: Mapping[str, Any]) -> Limits:
    if not document:
        return Limits()
    _reject_unknown(document, ("parallel", "max_active", "budget"), "limits")
    parallel = _flag(document, "parallel", "limits", False)
    max_active = _positive_int(document, "max_active", "limits", SEQUENTIAL_MAX_ACTIVE)
    budget_document = _section(document, "budget", required=False)
    if not parallel and max_active != SEQUENTIAL_MAX_ACTIVE:
        _fail(
            f"limits.max_active is {max_active} while limits.parallel is false; "
            "raising the ceiling is what the switch is for, so set it or leave "
            f"the ceiling at {SEQUENTIAL_MAX_ACTIVE}"
        )
    if parallel and not budget_document:
        _fail(
            "limits.parallel is true without limits.budget; an opt-in that names "
            "no ceiling for cpu, memory, processes, disk and time is an unbounded "
            "spawn"
        )
    return Limits(
        parallel=parallel,
        max_active=max_active,
        budget=_budget(budget_document) if budget_document else None,
    )


def _distrobox(document: Mapping[str, Any]) -> DistroboxConfig:
    _reject_unknown(
        document,
        (
            "image",
            "container_prefix",
            "extra_mounts",
            "provision",
            "accept_host_home_mount",
            "gui",
            "vcpus",
            "memory_mb",
            "pids",
            "disk_bytes",
        ),
        "distrobox",
    )
    gui = _text(document, "gui", "distrobox", "virtual")
    if gui not in GUI_MODES:
        _fail(
            f"distrobox.gui must be one of {', '.join(GUI_MODES)}, got {gui!r}; "
            "which screen the application uses is an explicit choice"
        )
    mounts = tuple(document.get("extra_mounts") or ())
    for mount in mounts:
        if not isinstance(mount, str):
            _fail("distrobox.extra_mounts must contain only strings")
    return DistroboxConfig(
        image=_text(document, "image", "distrobox"),
        container_prefix=_text(document, "container_prefix", "distrobox"),
        extra_mounts=mounts,
        provision=_provision(document, "distrobox"),
        accept_host_home_mount=_flag(
            document, "accept_host_home_mount", "distrobox", False
        ),
        gui=gui,
        vcpus=_positive_int(document, "vcpus", "distrobox", 2),
        memory_mb=_positive_int(document, "memory_mb", "distrobox", 4096),
        pids=_positive_int(document, "pids", "distrobox", 2048),
        disk_bytes=_positive_int(
            document, "disk_bytes", "distrobox", 8 * 1024 * 1024 * 1024
        ),
    )


def _vm(document: Mapping[str, Any]) -> VmConfig:
    _reject_unknown(
        document,
        (
            "provider",
            "provider_version",
            "stack_prefix",
            "base_image",
            "base_image_sha256",
            "guest_user",
            "venv",
            "pulumi_binary",
            "connect_uri",
            "pool",
            "overlay_size_bytes",
            "memory_mb",
            "vcpus",
            "graphics",
            "video",
            "listen_address",
            "transport",
            "network",
            "ssh_port",
            "address_timeout_seconds",
            "egress",
            "cpu_mode",
            "qemu_agent",
            "provision",
        ),
        "vm",
    )
    base_image = Path(_text(document, "base_image", "vm"))
    if not base_image.is_absolute():
        _fail("field vm.base_image must be an absolute path")
    # Absent means "whatever the build that produced this image recorded beside
    # it". A packer build is not reproducible byte for byte, so a digest in a
    # committed configuration would be wrong after every rebuild.
    digest = _text(document, "base_image_sha256", "vm", "")
    if digest and (
        len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        _fail("field vm.base_image_sha256 must be a lowercase content digest")
    cpu_mode = _text(document, "cpu_mode", "vm", "host-passthrough")
    if cpu_mode not in CPU_MODES:
        _fail(
            f"vm.cpu_mode must be one of {', '.join(CPU_MODES)}, got {cpu_mode!r}"
        )
    venv = Path(_text(document, "venv", "vm"))
    if not venv.is_absolute():
        _fail("field vm.venv must be an absolute path to the pinned environment")
    return VmConfig(
        provider=_text(document, "provider", "vm"),
        provider_version=_text(document, "provider_version", "vm"),
        stack_prefix=_text(document, "stack_prefix", "vm"),
        base_image=base_image,
        base_image_sha256=digest,
        guest_user=_text(document, "guest_user", "vm"),
        venv=venv,
        pulumi_binary=_text(document, "pulumi_binary", "vm", "pulumi"),
        connect_uri=_text(document, "connect_uri", "vm", "qemu:///system"),
        pool=_text(document, "pool", "vm", "dely-cycle"),
        overlay_size_bytes=_positive_int(
            document, "overlay_size_bytes", "vm", 24 * 1024 * 1024 * 1024
        ),
        memory_mb=_positive_int(document, "memory_mb", "vm", 4096),
        vcpus=_positive_int(document, "vcpus", "vm", 2),
        graphics=_text(document, "graphics", "vm", "vnc"),
        video=_text(document, "video", "vm", "virtio"),
        listen_address=_text(document, "listen_address", "vm", "127.0.0.1"),
        transport=_text(document, "transport", "vm", "ssh"),
        network=_text(document, "network", "vm", "default"),
        ssh_port=_positive_int(document, "ssh_port", "vm", 22),
        address_timeout_seconds=_positive_int(
            document, "address_timeout_seconds", "vm", 600
        ),
        egress=_flag(document, "egress", "vm", True),
        cpu_mode=cpu_mode,
        qemu_agent=_flag(document, "qemu_agent", "vm", False),
        provision=_provision(document, "vm"),
    )


def from_document(document: Mapping[str, Any]) -> RunConfig:
    """Validate a loaded document and return the typed configuration."""
    if not isinstance(document, Mapping):
        _fail("the configuration must be a mapping")
    refuse_secrets(document)
    _reject_unknown(document, _TOP_LEVEL, "the configuration")

    backend = _text(document, "backend", "")
    if backend not in BACKENDS:
        _fail(f"backend must be one of {', '.join(BACKENDS)}, got {backend!r}")

    artifact_root = _absolute(document, "artifact_root")
    state_root = _absolute(document, "state_root")
    if artifact_root.is_relative_to(state_root):
        _fail("artifact_root must not be inside state_root; cleanup would remove the evidence")
    if state_root.is_relative_to(artifact_root):
        _fail("state_root must not be inside artifact_root; cleanup would remove the evidence")

    task = _task(_section(document, "task", required=True))
    distrobox_document = _section(document, "distrobox", required=backend == "distrobox")
    vm_document = _section(document, "vm", required=backend == "vm")
    if backend == "distrobox" and not distrobox_document:
        _fail("backend distrobox requires a distrobox section")
    if backend == "vm" and not vm_document:
        _fail("backend vm requires a vm section")

    return RunConfig(
        backend=backend,
        tested_revision=_text(document, "tested_revision", ""),
        dely_revision=_text(document, "dely_revision", ""),
        claude_code_version=_text(document, "claude_code_version", ""),
        timeout_seconds=_positive_int(document, "timeout_seconds", ""),
        artifact_root=artifact_root,
        state_root=state_root,
        project=_project(_section(document, "project", required=True)),
        task=task,
        check=_check(_section(document, "check", required=True), task),
        auth=_auth(_section(document, "auth", required=True)),
        orca=_orca(_section(document, "orca", required=True)),
        skills=_skills(_section(document, "skills", required=False)),
        review=_review(_section(document, "review", required=False)),
        limits=_limits(_section(document, "limits", required=False)),
        distrobox=_distrobox(distrobox_document) if distrobox_document else None,
        vm=_vm(vm_document) if vm_document else None,
    )


def load(path: Path, *, yaml_module: Any = _UNSET) -> RunConfig:
    """Load a configuration from a JSON or YAML file."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        parser = _DEFAULT_YAML if yaml_module is _UNSET else yaml_module
        if parser is None:
            _fail(
                f"{path} needs a yaml parser this host does not have; "
                "write the same document as json and pass that instead"
            )
        document = parser.safe_load(raw)
    else:
        document = json.loads(raw)
    return from_document(document).with_source(path)
