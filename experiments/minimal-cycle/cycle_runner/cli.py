"""The command line: preflight a backend, or run one cycle.

Every failure this tool can reasonably meet — an absent configuration, an
invalid one, a backend this host cannot run — is reported as a sentence and an
exit code rather than as a traceback.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, TextIO

from . import (
    adapters,
    admission,
    hostregistry,
    config as config_module,
    ids,
    lifecycle,
    manifest,
    proc,
    processes,
    residue,
)
from .status import RunStatus, exit_code

USAGE_EXIT = 2


def _load(path: Path) -> config_module.RunConfig:
    if not Path(path).is_file():
        raise config_module.ConfigError(f"no configuration at {path}")
    return config_module.load(Path(path))


def _tool_versions() -> dict[str, str]:
    versions = {"runner": "minimal-cycle", "python": sys.version.split()[0]}
    for name, argv in (
        ("distrobox", ["distrobox", "--version"]),
        ("podman", ["podman", "--version"]),
        ("qemu_img", ["qemu-img", "--version"]),
        ("virsh", ["virsh", "--version"]),
        ("pulumi", ["pulumi", "version"]),
        ("git", ["git", "--version"]),
    ):
        outcome = proc.run(argv, timeout=60, context="host")
        if outcome.ok and outcome.stdout.strip():
            versions[name] = outcome.stdout.strip().splitlines()[-1]
    return versions


def _preflight(arguments, stdout: TextIO, stderr: TextIO) -> int:
    run_config = _load(arguments.config)
    adapter = adapters.build(
        run_config=run_config,
        run_id=arguments.run_id or _mint(),
    )
    report = adapter.preflight()
    if arguments.json:
        print(json.dumps(report.to_document(), indent=2, sort_keys=True), file=stdout)
    else:
        print(f"backend: {report.backend}", file=stdout)
        for finding in report.findings:
            mark = "ok    " if finding.ok else "BLOCK "
            print(f"  {mark} {finding.name}: {finding.detail}", file=stdout)
        print(
            "usable on this host" if report.ok else "not usable on this host",
            file=stdout,
        )
    return 0 if report.ok else exit_code(RunStatus.BLOCKED)


def _mint() -> str:
    return ids.mint_run_id(now=datetime.now(timezone.utc), host_name=socket.gethostname())


def _run(arguments, stdout: TextIO, stderr: TextIO) -> int:
    run_config = _load(arguments.config)
    run_id = arguments.run_id or _mint()
    if not ids.is_run_id(run_id):
        raise config_module.ConfigError(
            f"{run_id!r} is not a run identifier of the documented shape"
        )
    adapter = adapters.build(run_config=run_config, run_id=run_id)
    outcome = lifecycle.run_cycle(
        run_config=run_config,
        adapter=adapter,
        run_id=run_id,
        tool_versions=_tool_versions(),
        survey=processes.survey_and_stop,
    )
    result = outcome.run_result
    if arguments.json:
        print(
            json.dumps(result.to_document(), indent=2, sort_keys=True), file=stdout
        )
    else:
        print(f"run_id:    {result.run_id}", file=stdout)
        print(f"backend:   {result.backend}", file=stdout)
        print(f"status:    {result.status.value}", file=stdout)
        print(f"identity:  {result.identity.verdict} — {result.identity.reason}", file=stdout)
        print(f"check:     {result.check.status.value} (exit {result.check.exit_code})", file=stdout)
        print(f"export:    {result.export.status.value}", file=stdout)
        print(f"cleanup:   {result.cleanup.status.value} — {result.cleanup.reason}", file=stdout)
        print(f"artifacts: {outcome.artifact_dir}", file=stdout)
        if result.failure_classification:
            print(f"why:       {result.failure_classification}", file=stdout)
    return outcome.exit_code


def _run_paths(run_config: config_module.RunConfig, run_id: str) -> list[str]:
    """The paths that belong to one run, named without needing its handle."""
    return [
        str(run_config.state_root / run_id),
        str(run_config.artifact_root / run_id),
    ]


def _leases(arguments, stdout: TextIO, stderr: TextIO) -> int:
    run_config = _load(arguments.config)
    backends = (
        config_module.BACKENDS if arguments.all else (run_config.backend,)
    )
    records = [
        record
        for backend in backends
        for record in admission.read_leases(run_config.state_root, backend)
    ]
    if arguments.json:
        print(
            json.dumps(
                [record.to_document() for record in records], indent=2, sort_keys=True
            ),
            file=stdout,
        )
        return 0
    if not records:
        print(
            f"no environment holds a slot on {', '.join(backends)}",
            file=stdout,
        )
        return 0
    for record in records:
        print(f"{record.backend:10} {record.state:10} {record.describe()}", file=stdout)
    occupied = sum(1 for record in records if record.occupies)
    ceiling = run_config.limits.effective_max_active
    print(
        f"{occupied} slot(s) occupied against a ceiling of {ceiling} per backend",
        file=stdout,
    )
    return 0


def _release(arguments, stdout: TextIO, stderr: TextIO) -> int:
    """Clear one lease, once a survey shows nothing of that run is still running."""
    run_config = _load(arguments.config)
    backend = arguments.backend or run_config.backend
    paths = _run_paths(run_config, arguments.run_id)
    holders = processes.holding(paths)
    try:
        admission.clear(
            root=run_config.state_root,
            backend=backend,
            run_id=arguments.run_id,
            holders=[f"pid {item.pid} {item.command[:80]}" for item in holders],
        )
    except admission.AdmissionRefused as refusal:
        print(f"refused: {refusal}", file=stderr)
        return exit_code(RunStatus.BLOCKED)
    print(
        f"released the {backend} slot held by {arguments.run_id}; "
        f"no process holds {' or '.join(paths)}",
        file=stdout,
    )
    return 0


def _adapter_for(
    run_config: config_module.RunConfig, backend: str, run_id: str
) -> adapters.BackendAdapter:
    """Build the adapter for one past run, whichever backend that run used."""
    if backend != run_config.backend:
        run_config = dataclasses.replace(run_config, backend=backend)
    return adapters.build(run_config=run_config, run_id=run_id)


def _establish_gone(
    run_config: config_module.RunConfig,
    run_id: str,
    records: Sequence[admission.LeaseRecord],
    left: residue.StateResidue,
) -> dict[str, list[str]]:
    """Ask, three separate ways, whether anything of this run is still here.

    Is a process of it still running, does its lease still name a live owner,
    and is its container or domain still on this host. Each answer lands in
    `still_present` or, when this host could not put the question at all, in
    `unanswered` — because a question that cannot be asked is not one that was
    answered no, and nothing is removed on that difference.
    """
    found: dict[str, list[str]] = {
        "holders": [
            f"pid {item.pid} {item.command[:80]}"
            for item in processes.holding(_run_paths(run_config, run_id))
        ],
        "still_present": [],
        "unanswered": [],
        "observed": [],
    }
    backend = ""
    for record in records:
        backend = backend or record.backend
        if record.state == admission.HELD:
            found["still_present"].append(
                f"a {record.backend} lease held by live pid {record.pid}"
            )
        elif record.state == admission.UNREADABLE:
            found["unanswered"].append(
                f"the {record.backend} lease for it cannot be read: {record.reason}"
            )
        else:
            found["observed"].append(
                f"its {record.backend} lease is {record.state}, so no process owns it"
            )
    backend = backend or run_config.backend
    found["backend"] = [backend]
    if backend != run_config.backend:
        found["observed"].append(
            f"its lease says it was a {backend} run, so that is what was asked"
        )
    elif not records:
        found["observed"].append(
            f"it has no lease, so the configured {backend} backend was asked"
        )
    wrote = residue.backend_that_wrote_it(left)
    if wrote and wrote != backend:
        found["unanswered"].append(
            f"what it left was written by the {wrote} backend and the "
            f"{backend} backend is what this configuration can ask; run this "
            f"again with a {wrote} configuration over the same state root"
        )
        return found
    try:
        adapter = _adapter_for(run_config, backend, run_id)
    except (ValueError, RuntimeError) as error:
        found["unanswered"].append(
            f"no {found['backend'][0]} adapter could be built from this "
            f"configuration, so its environment cannot be asked about: {error}"
        )
        return found
    observable, detail = adapter.can_see_environment()
    if not observable:
        found["unanswered"].append(
            f"whether its {adapter.name} environment still exists could not be "
            f"asked: {detail}"
        )
        return found
    found["observed"].append(detail)
    handle = adapter.plan_handle()
    for resource in handle.per_run_resources if handle else ():
        if resource.kind == "path":
            continue
        try:
            present = adapter.resource_exists(resource)
        except (OSError, RuntimeError, ValueError) as error:
            found["unanswered"].append(
                f"whether {resource} is still on this host could not be "
                f"established: {error}"
            )
            continue
        if present:
            found["still_present"].append(str(resource))
        else:
            found["observed"].append(f"{resource} is gone")
    return found


def _residue(arguments, stdout: TextIO, stderr: TextIO) -> int:
    """Report what runs that never finished left under the state root.

    Cleanup only runs for a run that reached its end, so a killed or
    interrupted one leaves its per-run state — a transport private key among
    it, on the machine backend — with nothing that ever notices. This is the
    command that notices. `--discard` removes one named run's state, and only
    after the three questions above have all been answered.
    """
    run_config = _load(arguments.config)
    root = run_config.state_root
    by_run: dict[str, list[admission.LeaseRecord]] = {}
    for backend in config_module.BACKENDS:
        for record in admission.read_leases(root, backend):
            by_run.setdefault(record.run_id, []).append(record)
    known = set(residue.run_identifiers(root)) | set(by_run)
    wanted = [arguments.run_id] if arguments.run_id else sorted(known)

    reports = []
    for run_id in wanted:
        left = residue.survey(root, run_id)
        found = _establish_gone(run_config, run_id, by_run.get(run_id, []), left)
        reports.append(
            {
                "run_id": run_id,
                "backend": found["backend"][0],
                "state": left.to_document(),
                "still_present": found["still_present"],
                "unanswered": found["unanswered"],
                "holders": found["holders"],
                "observed": found["observed"],
                "removable": bool(
                    left.present
                    and not found["still_present"]
                    and not found["unanswered"]
                    and not found["holders"]
                ),
            }
        )

    if arguments.discard:
        return _discard(arguments, run_config, reports, stdout, stderr)

    if arguments.json:
        print(json.dumps(reports, indent=2, sort_keys=True), file=stdout)
    else:
        _print_residue(reports, root, stdout)
    left_behind = [item for item in reports if item["state"]["present"]]
    return exit_code(RunStatus.BLOCKED) if left_behind else 0


def _print_residue(reports: Sequence[dict], root: Path, stdout: TextIO) -> None:
    """Print what each run left, and what says whether it is over."""
    present = [item for item in reports if item["state"]["present"]]
    if not present:
        for item in reports:
            print(item["state"]["detail"], file=stdout)
        if not reports:
            print(f"no run has left state under {root}", file=stdout)
        return
    keyed = [item for item in present if item["state"]["carries_key_material"]]
    for item in present:
        print(f"{item['run_id']}: {item['state']['detail']}", file=stdout)
        for entry in item["state"]["entries"]:
            shape = "dir " if entry["is_directory"] else "file"
            print(
                f"    {shape} {entry['name']:16} {entry['file_count']:>6} file(s)"
                f"  {entry['role']}",
                file=stdout,
            )
            for relative in entry["key_material"]:
                print(f"         {residue.KEY_MATERIAL}: {relative}", file=stdout)
        for line in item["observed"]:
            print(f"    seen:        {line}", file=stdout)
        for line in item["holders"]:
            print(f"    still here:  {line}", file=stdout)
        for line in item["still_present"]:
            print(f"    still here:  {line}", file=stdout)
        for line in item["unanswered"]:
            print(f"    not known:   {line}", file=stdout)
        print(
            "    nothing of this run is still on this host; "
            f"--run-id {item['run_id']} --discard removes what it left"
            if item["removable"]
            else "    this run is not established to be over; nothing is offered",
            file=stdout,
        )
        print("", file=stdout)
    print(
        f"{len(present)} run(s) left state under {root}, "
        f"{len(keyed)} of them carrying private key material",
        file=stdout,
    )


def _discard(
    arguments,
    run_config: config_module.RunConfig,
    reports: Sequence[dict],
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Remove one named run's state, after printing what is about to go."""
    if not arguments.run_id:
        print(
            "refused: --discard removes one named run's state and needs "
            "--run-id; nothing is removed across a whole state root at once",
            file=stderr,
        )
        return exit_code(RunStatus.BLOCKED)
    item = reports[0]
    if arguments.json:
        print(json.dumps(reports, indent=2, sort_keys=True), file=stdout)
    else:
        _print_residue(reports, run_config.state_root, stdout)
    try:
        removed = residue.discard(
            root=run_config.state_root,
            run_id=arguments.run_id,
            holders=item["holders"],
            still_present=item["still_present"],
            unanswered=item["unanswered"],
        )
    except residue.ResidueRefused as refusal:
        print(f"refused: {refusal}", file=stderr)
        return exit_code(RunStatus.BLOCKED)
    print(
        f"removed {removed.path}: {removed.file_count} file(s)"
        + (
            f", including {residue.KEY_MATERIAL} at "
            + ", ".join(removed.key_material)
            if removed.key_material
            else ""
        ),
        file=stdout,
    )
    print(
        "the lease is separate accounting: `release` is what gives the slot back",
        file=stdout,
    )
    return 0


def _host_registry(arguments, stdout: TextIO, stderr: TextIO) -> int:
    """Report what the operator's own Orca registry holds for this runner's runs.

    A run's own artifacts answer this for that run. This answers it for the
    host as a whole, which is what shows an entry an earlier run left behind.
    """
    run_config = _load(arguments.config)
    markers = [str(run_config.state_root)]
    markers.extend(arguments.marker or [])
    report = hostregistry.fingerprint(None, markers=markers)
    if arguments.json:
        print(json.dumps(report, indent=2, sort_keys=True), file=stdout)
        return 0
    print(f"registry files: {report['file_count']}", file=stdout)
    for name, size in sorted((report.get("collection_sizes") or {}).items()):
        print(f"  {name:24} {size}", file=stdout)
    naming = report.get("entries_naming_this_run") or []
    if not naming:
        print(
            "no registry file names anything under "
            + ", ".join(markers),
            file=stdout,
        )
        return 0
    print("", file=stdout)
    print("these registry files name this runner's per-run paths:", file=stdout)
    for entry in naming:
        print(f"  {entry['name']:28} {entry['names_this_run']} occurrence(s)", file=stdout)
    print(
        "\nNothing here removes them: they are in the operator's own profile and "
        "only they can decide.",
        file=stdout,
    )
    return exit_code(RunStatus.BLOCKED)


def _schema(arguments, stdout: TextIO, stderr: TextIO) -> int:
    print(
        json.dumps(manifest.load_schema(), indent=2, sort_keys=True), file=stdout
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the runner."""
    parser = argparse.ArgumentParser(
        prog="run-cycle",
        description="Run one minimal proof cycle on a disposable environment.",
    )
    subcommands = parser.add_subparsers(dest="command")

    preflight = subcommands.add_parser(
        "preflight", help="report whether this host can run the configured backend"
    )
    preflight.add_argument("--config", required=True, type=Path)
    preflight.add_argument("--run-id")
    preflight.add_argument("--json", action="store_true")
    preflight.set_defaults(handler=_preflight)

    run = subcommands.add_parser("run", help="run one cycle end to end")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--run-id", help="pin the run identifier instead of minting one")
    run.add_argument("--json", action="store_true")
    run.set_defaults(handler=_run)

    leases = subcommands.add_parser(
        "leases", help="show which runs hold this host's environment slots"
    )
    leases.add_argument("--config", required=True, type=Path)
    leases.add_argument("--all", action="store_true", help="every backend, not just this one")
    leases.add_argument("--json", action="store_true")
    leases.set_defaults(handler=_leases)

    release = subcommands.add_parser(
        "release",
        help="clear one slot, after checking that nothing of that run is still running",
    )
    release.add_argument("--config", required=True, type=Path)
    release.add_argument("--run-id", required=True)
    release.add_argument("--backend", choices=sorted(config_module.BACKENDS))
    release.set_defaults(handler=_release)

    leftovers = subcommands.add_parser(
        "residue",
        help="report what runs that never finished left under the state root",
    )
    leftovers.add_argument("--config", required=True, type=Path)
    leftovers.add_argument("--run-id", help="one run, instead of every one found")
    leftovers.add_argument(
        "--discard",
        action="store_true",
        help="remove that run's state, once nothing of the run is still here",
    )
    leftovers.add_argument("--json", action="store_true")
    leftovers.set_defaults(handler=_residue)

    registry = subcommands.add_parser(
        "host-registry",
        help="report whether the operator's own Orca registry names this runner's runs",
    )
    registry.add_argument("--config", required=True, type=Path)
    registry.add_argument(
        "--marker", action="append", help="an extra string to search the registry for"
    )
    registry.add_argument("--json", action="store_true")
    registry.set_defaults(handler=_host_registry)

    schema = subcommands.add_parser("schema", help="print the manifest schema")
    schema.set_defaults(handler=_schema)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the command line and return the process exit code."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(arguments, "handler", None):
        parser.print_usage(stderr)
        return USAGE_EXIT
    try:
        return arguments.handler(arguments, stdout, stderr)
    except (config_module.ConfigError, manifest.ManifestError) as error:
        print(f"error: {error}", file=stderr)
        return USAGE_EXIT
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {type(error).__name__}: {error}", file=stderr)
        return exit_code(RunStatus.ERROR)
