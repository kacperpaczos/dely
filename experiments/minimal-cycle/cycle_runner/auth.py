"""Getting the environment authenticated without leaving a secret behind.

Exactly one of the three declared methods runs. None of them writes a
credential value, a length or a prefix into a receipt, a manifest, a log or
this repository, and the copied material is removed from the environment and
verified gone before cleanup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .adapters.base import BackendAdapter, EnvironmentHandle
from .config import RunConfig
from .result import AuthRecord
from .status import PhaseStatus

SETTINGS_RELATIVE = ".claude/settings.json"


def settings_document(run_config: RunConfig) -> dict[str, str]:
    """Return what the configured auth method declares in the per-run settings."""
    if run_config.auth.mode != "api_key_helper":
        return {}
    return {"apiKeyHelper": " ".join(run_config.auth.helper_argv)}


def _environment_path(handle: EnvironmentHandle, relative: str) -> str:
    return str(Path(handle.home_path) / relative)


def _existing_login(
    run_config: RunConfig,
    adapter: BackendAdapter,
    handle: EnvironmentHandle,
    host_home: Path,
) -> tuple[AuthRecord, dict[str, str]]:
    record = AuthRecord(
        mode="existing_login",
        reference=run_config.auth.reference,
        target=str(Path(handle.home_path)),
        entries=list(run_config.auth.allowlist),
    )
    missing = [
        entry for entry in run_config.auth.allowlist if not (host_home / entry).is_file()
    ]
    if missing:
        record.status = PhaseStatus.BLOCKED
        record.detail = (
            "the host does not carry every allowlisted entry: " + ", ".join(missing)
        )
        return record, {}
    for entry in run_config.auth.allowlist:
        content = (host_home / entry).read_text(encoding="utf-8")
        adapter.write_file(_environment_path(handle, entry), content, mode=0o600)
    record.status = PhaseStatus.OK
    record.detail = (
        f"{len(record.entries)} allowlisted entry(s) copied into the per-run home "
        "with owner-only permissions"
    )
    return record, {}


def _short_lived_token(
    run_config: RunConfig,
    handle: EnvironmentHandle,
    environ: Mapping[str, str],
) -> tuple[AuthRecord, dict[str, str]]:
    variable = run_config.auth.token_env or ""
    record = AuthRecord(
        mode="short_lived_token",
        reference=run_config.auth.reference,
        target="the worker process environment",
        entries=[variable],
    )
    value = environ.get(variable, "")
    if not value:
        record.status = PhaseStatus.BLOCKED
        record.detail = (
            f"the variable {variable} carries no short-lived token on this host; "
            "the run stops rather than prompting for an interactive login"
        )
        return record, {}
    record.status = PhaseStatus.OK
    record.removed_after_run = True
    record.detail = (
        f"the value of {variable} is passed to the worker process for this run only "
        "and is never written to a file, an image, a seed or a log"
    )
    return record, {variable: value}


def _api_key_helper(
    run_config: RunConfig,
    adapter: BackendAdapter,
    handle: EnvironmentHandle,
) -> tuple[AuthRecord, dict[str, str]]:
    record = AuthRecord(
        mode="api_key_helper",
        reference=run_config.auth.reference,
        target=_environment_path(handle, SETTINGS_RELATIVE),
        entries=[SETTINGS_RELATIVE],
    )
    adapter.write_file(
        _environment_path(handle, SETTINGS_RELATIVE),
        json.dumps(settings_document(run_config), indent=2) + "\n",
        mode=0o600,
    )
    record.status = PhaseStatus.OK
    record.removed_after_run = False
    record.detail = (
        "the helper command is declared in the per-run settings; no credential "
        "value passes through the runner"
    )
    return record, {}


def bootstrap(
    *,
    run_config: RunConfig,
    adapter: BackendAdapter,
    handle: EnvironmentHandle,
    host_home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[AuthRecord, dict[str, str]]:
    """Run the one configured auth method and return its record and overlay."""
    host_home = Path(host_home if host_home is not None else Path.home())
    environ = os.environ if environ is None else environ
    mode = run_config.auth.mode
    if mode == "existing_login":
        return _existing_login(run_config, adapter, handle, host_home)
    if mode == "short_lived_token":
        return _short_lived_token(run_config, handle, environ)
    return _api_key_helper(run_config, adapter, handle)


#: The command that asks the agent, in the environment, whether it is signed in.
#: It reads whatever the bootstrap put there, so it answers the question the
#: bootstrap receipt cannot: not "was a file copied" but "does it work".
STATUS_ARGV = ("claude", "auth", "status", "--json")

#: The only fields kept from that answer. The rest names a person and an
#: organisation, and a run's artifacts are shared.
STATUS_FIELDS = ("loggedIn", "authMethod", "apiProvider", "subscriptionType")


def read_status(stdout: str) -> dict[str, Any]:
    """Return the non-identifying part of the agent's own answer."""
    for candidate in (stdout.strip(), *stdout.splitlines()):
        text = candidate.strip()
        if not text.startswith("{"):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            return {
                name: parsed[name] for name in STATUS_FIELDS if name in parsed
            }
    return {}


def judge_status(
    *, ok: bool, timed_out: bool, status: Mapping[str, Any]
) -> tuple[bool, str]:
    """Say whether what the bootstrap put there actually signs the agent in."""
    if timed_out:
        return False, (
            "the agent did not answer whether it is signed in before the deadline"
        )
    if not status:
        return False, (
            "the agent gave no readable answer about whether it is signed in, so "
            "nothing here knows if the bootstrap worked"
        )
    if not status.get("loggedIn"):
        return False, (
            "the agent in the environment is not signed in; what the bootstrap "
            "put there is absent, expired or not what this agent reads"
        )
    method = status.get("authMethod") or "an unnamed method"
    return True, (
        f"the agent in the environment reports itself signed in through {method}"
    )


#: Asked only when the agent did not answer. Each line is bounded, so the
#: diagnosis cannot itself hang, and each one separates a different cause: a
#: binary that does not run at all, a name that does not resolve, a route that
#: does not exist, and a connection that is refused rather than swallowed.
DIAGNOSIS_SCRIPT = r"""
set -u
say() { printf '\n== %s ==\n' "$1"; }
say "claude --version"
timeout 20 claude --version 2>&1 || printf 'exit %s\n' "$?"
say "default routes"
ip route show default 2>&1 || printf 'no ip command\n'
say "name resolution"
timeout 15 getent hosts api.anthropic.com 2>&1 || printf 'exit %s\n' "$?"
say "reachability"
timeout 20 curl -sS -o /dev/null -w 'http %{http_code} in %{time_total}s\n' \
    https://api.anthropic.com/ 2>&1 || printf 'exit %s\n' "$?"
say "claude auth status again, bounded"
timeout 30 claude auth status --json 2>&1 || printf 'exit %s\n' "$?"
"""


def diagnosis_argv() -> list[str]:
    """Return the command that asks why the agent did not answer."""
    return ["sh", "-c", DIAGNOSIS_SCRIPT, "auth-diagnosis"]


def verify(
    *,
    adapter: BackendAdapter,
    record: AuthRecord,
    timeout: float = 120.0,
    env: Mapping[str, str] | None = None,
) -> AuthRecord:
    """Ask the environment's own agent whether the bootstrap actually worked.

    This is deliberately a second receipt. Copying a file and the file working
    are different claims, and a run that only ever showed the first one has not
    shown the second.
    """
    if record.status is not PhaseStatus.OK:
        return record
    outcome = adapter.execute(
        list(STATUS_ARGV),
        timeout=timeout,
        env=dict(env or {}),
        extra_values=tuple(value for value in (env or {}).values() if value),
    )
    status = read_status(outcome.stdout)
    works, detail = judge_status(
        ok=outcome.ok, timed_out=outcome.timed_out, status=status
    )
    record.verified = works
    record.observed = dict(status)
    record.verify_detail = detail
    if not works:
        record.status = PhaseStatus.BLOCKED
    return record


def teardown(
    *, adapter: BackendAdapter, handle: EnvironmentHandle, record: AuthRecord
) -> AuthRecord:
    """Remove copied auth material from the environment and verify it is gone."""
    if record.mode != "existing_login" or record.status is not PhaseStatus.OK:
        record.removed_after_run = record.mode == "short_lived_token"
        return record
    paths = [_environment_path(handle, entry) for entry in record.entries]
    adapter.execute(["rm", "-f", *paths], timeout=60)
    survey = adapter.execute(
        [
            "sh",
            "-c",
            'for candidate in "$@"; do if [ -e "$candidate" ]; then '
            'printf "present:%s\\n" "$candidate"; fi; done',
            "auth-teardown",
            *paths,
        ],
        timeout=60,
    )
    still_present = [line for line in survey.stdout.splitlines() if line.startswith("present:")]
    record.removed_after_run = not still_present
    record.detail = (
        "the copied auth material was removed from the per-run home and verified gone"
        if record.removed_after_run
        else "auth material is still present in the environment: " + ", ".join(still_present)
    )
    return record
