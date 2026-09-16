# Minimal cycle runner

One Python runner drives one minimal proof cycle on a disposable environment,
through one of two backends, and answers a single narrow question:

> Can a fresh environment be created, driven through Orca to one Claude Code
> worker on a clean copy of a project, handed to a second agent that reviews
> what the first one wrote, checked independently, exported to the host, and
> destroyed — without losing the evidence, without quietly falling back onto
> the host, and without leaving anything of itself on it?

It is not a benchmark, not an evaluation of a model or of a tester, and not a
task matrix. It measures plumbing.

## What is here

```
run-cycle                     the command line
schema.json                   the required shape of a run manifest
config.distrobox.example.yaml an example configuration for the container backend
config.vm.example.yaml        an example configuration for the machine backend
cycle_runner/                 the runner
cycle_runner/adapters/        one adapter per backend, behind one interface
fixtures/evidence-task/       the one small task the worker is given
tests/                        the suite, including the counterexamples
evidence/                     what the runner actually did on one host
```

The runner uses the Python standard library at runtime. A configuration may be
written as JSON always, or as YAML when a parser is importable; the error names
the alternative rather than parsing half a document.

## Running it

Once per host:

```bash
cd experiments/minimal-cycle
host/prepare-host                  # pinned pulumi, local state, image, pool, project source
host/packer/build-tool-image       # the machine backend's tool image
```

Then, for either backend, with the shipped configuration as it stands:

```bash
./run-cycle preflight --config config.distrobox.example.yaml
./run-cycle run       --config config.distrobox.example.yaml
```

The examples carry no placeholders. A test holds them to the versions
`host/versions.json` pins, so an example cannot drift away from the host it was
prepared for.

Two more commands exist for when a run does not end cleanly:

```bash
./run-cycle leases        --config <config>   # which run holds this host's slot
./run-cycle release       --config <config> --run-id <id>
./run-cycle host-registry --config <config>   # what the operator's own Orca knows
```

`preflight` reports, fact by fact, whether this host can run the configured
backend, and exits non-zero when it cannot. `run` mints a run identifier,
performs the whole cycle, and exits with the code its status maps to. Pass
`--run-id` to pin the identifier, and `--json` to get the machine-readable form
of either command.

The suite needs nothing installed:

```bash
python3 -m unittest discover -s tests -t . -v
```

## The cycle

```
prepare -> create -> bootstrap -> identity -> task -> review -> check
        -> collect -> export -> cleanup -> close
```

Two orderings are the point of the whole runner.

**Evidence is exported before anything is destroyed.** `collect` brings the
project tree back to the host, `export` writes every artifact and then re-reads
each one from its host path and recomputes its digest. Only an export where
every required artifact matches releases the run to stop and destroy anything.
An unconfirmed export, or an unconfirmed stop, leaves the environment standing
and records residue with the reason.

**The task runs only after the identity gate.** The same shell probe runs on the
host and inside the environment. A result that carries no environment marker and
repeats the host's own name, machine identity and home is a host fallback, and a
host fallback stops the run. So does finding no Orca inside the environment: the
run is blocked, never redirected to the host's installation.

Three more gates were added after runs on this host went wrong in ways nothing
would have caught.

**Nothing is created before this host says it has room.** One environment per
backend, counted under a lock two separate runners share, so two starting at the
same instant cannot both read "nothing active". A run whose process died before
confirming its cleanup keeps its slot, and so does one that ended in residue:
the next run is refused until somebody has looked. Parallel runs are possible,
but only as a switch with a ceiling and a budget for cpu, memory, processes,
disk and time, and the container manager is told the same numbers the budget
counts.

**The skills the configuration pins have to actually be there.** After
provisioning, the runner asks the environment where each `SKILL.md` is and what
it hashes to, and compares. An install that fetched what was current instead of
what was pinned, or landed where the agent never looks, blocks the run.

**The environment must not register itself into the operator's own Orca.** The
host's registry is searched for this run's identifier, container or domain and
per-run paths — as bytes, so a repository entry, a worktree, the record that a
terminal exists and a row in the orchestration store are all caught the same
way. An entry naming the run makes the cleanup residue. Nothing here removes
it: it is in somebody's own profile.

**The review is a second agent, not a second pane.** When the implementer
settles, Control captures the diff inside the environment, takes its digest, and
starts a separate dispatch under the same Run whose prompt names the diff and
nothing else. The two dispatches must carry different identifiers and different
agent terminals; the digest must be unchanged when the review returns; and the
reviewer must report that digest. Layout, focus and panel position are not
consulted, because they show nothing about whose session is whose.

## Statuses

| Status | Exit | Meaning |
| --- | --- | --- |
| `SETTLED` | 0 | every phase ran, the check observed the marker, the export was confirmed and the per-run resources are gone |
| `ERROR` | 1 | the run completed but something it promised did not hold — usually the check |
| `TIMEOUT` | 2 | the task reached the configured deadline; the artifacts were still exported |
| `CANCELLED` | 3 | the run was stopped deliberately |
| `BLOCKED` | 4 | a gate refused: preflight, auth, the identity verdict, or a missing Orca |
| `CLEANUP_FAILED` | 5 | everything else held, but a per-run resource survived |
| `UNKNOWN` | 6 | the export was not confirmed, so nothing about the run rests on complete evidence |

A status name is a classification of the manifest, never a substitute for it.

## Artifacts

```
$artifact_root/<run_id>/
  manifest.json              the whole run, validated against schema.json
  preflight.json             what the host could and could not do
  backend-status.json        the environment and its declared resources
  auth-receipt.json          the method and its status, never its material
  first-run-state.json       the questions the agent was answered, by name
  admission.json             the slot this run held, and what else held one
  skills.json                each required skill, where it was and what it hashed to
  review.json                the handoff: the diff, the two agents, the verdict
  handoff-diff.patch         the diff the reviewer was given
  host-registry-before.json  the operator's own Orca registry, before
  host-registry-after.json   and after, with the question it was searched for
  dispatch/                  each orchestration reply, redacted, as the plane sent it
  host-before.json           the host before the run
  host-after.json            the host after it, and what changed
  identity/host-probe.txt    the probe as the host answered it
  identity/environment-probe.txt  the probe as the environment answered it
  identity/orca-status.json  what Orca reported inside the environment
  check.stdout / check.stderr  the independent check, as a process
  diff.patch                 what the task changed, computed on the host
  task-artifact/             the file the task was asked to produce
  logs/runner.log            the runner's own narration
  logs/commands.jsonl        every command, with timings and exit codes
  run-before-cleanup.json    the result as it stood when the export was taken
  export-receipt.json        each artifact re-read from the host, with its digest
  cleanup.json               what was removed, what was kept, what the run left
                             running on the host, and how each was checked
```

`manifest.json`, `cleanup.json` and `host-after.json` are written after cleanup,
so they are deliberately outside the export receipt: the receipt proves what the
host held *before* anything was destroyed, which is the only moment at which
that proof is worth anything.

Every stream is redacted on the way out. Redaction matches shapes — bearer
headers, key prefixes, compact web tokens, private-key blocks, absolute paths to
credential files, and any assignment whose key names a secret — so a credential
this run has never seen is still removed.

## The two backends

### Distrobox

A fresh box from a declared Distrobox Assemble manifest, with its own home and
its own copy of the project, created and destroyed through Assemble rather than
through a container manager this runner would have to become.

**What Distrobox does not give you.** Distrobox exists to integrate with the
host, and it mounts the invoking user's home directory into every box at its own
path. There is no flag that suppresses it. A separate per-run home limits which
state the run writes; it does not make the host's credentials unreachable from
inside the box.

The runner does not assert this either way. Preflight asks Distrobox what it
would actually mount, with `distrobox assemble create --dry-run`, and reads the
rendered container command. If the host home is mounted, the run is blocked
until `distrobox.accept_host_home_mount` records that the compromise is
deliberate — and the manifest then carries that acknowledgement.

Extra mounts are checked separately: a mount naming the host home, a directory
above it, or any known credential directory under it is refused outright.

`--unshare-all` is not written for you. It exists, it may conflict with the
graphical and message-bus access Orca needs, and choosing it is a decision to
make against a real Orca launch rather than a default to inherit.

### Virtual machine

A per-run libvirt domain declared with Pulumi in Python, over a qcow2 overlay
whose backing volume is a preserved tool image, with a cloud-init NoCloud seed
and an ssh transport. The guest is Ubuntu Server 24.04 LTS from Canonical's
cloud image, verified against the published checksum — a disk image for qemu,
never a container image.

Run `host/prepare-host` once. It installs the pinned Pulumi, points it at a
local file state backend, builds the python environment carrying the pinned
software development kit and libvirt provider, downloads and verifies the base
image, and declares a storage pool over the image directory. `--check` verifies
all of that and changes nothing.

`host/packer/build-tool-image` then builds the one tool image: it boots the
verified base under packer's qemu builder, installs the pinned Orca package, the
pinned Claude Code, a graphical session for the Orca window, and the guest
agent, then records a metadata file naming every version and digest that went
in. The build key is generated per build and removed from the image before
shutdown.

**Nothing about the provider is assumed.** The rendered program is parsed, every
provider class and keyword it names is collected, and each is checked against
the provider actually importable from the pinned environment. Preflight blocks
when a field does not exist and also when the check cannot be run at all. That
check has already earned its place: it caught a class name the program had
wrong before a single domain was created.

Preflight reads host facts rather than trusting configuration: that the Pulumi
state backend is a local file backend and not the hosted service, that the
storage pool and network are active, that the base image digest matches, and
that this emulator actually offers the configured graphics type. On the machine
this was built on, qemu has no spice at all — it offers sdl, vnc and dbus — so
the graphics finding is a refusal, not a formality.

Two facts about the guest's network are worth stating, because both were found
by running it. The domain takes a bridged interface for the runner's transport,
and a second, user-mode interface for the guest's own outbound traffic: a
bridged guest cannot reach the internet when the host routes through a virtual
private network or its firewall declines to forward, and a user-mode interface
is served by qemu itself and does not use that path. And an address is not
readiness — libvirt hands out the lease while the guest is still booting, so
creation is not finished until the guest answers a command and its first-boot
configuration has settled.

The base image is a shared resource, opened only as a backing volume, so it can
never appear in a destroy plan. The per-run overlay, seed, domain, stack and
state are all per-run and go together.

## Auth

Exactly one declared method runs, and none of them writes a credential value, a
length, or a prefix into any artifact.

- `existing_login` copies only the relative allowlist entries from the host home
  into the per-run home with owner-only permissions, and removes them before
  cleanup. An entry that is absent blocks the run. On Distrobox this buys less
  than it looks like, for the reason above.
- `short_lived_token` passes the value of one named variable to the worker
  process for the duration of the run. On Distrobox the name alone is forwarded
  to the container manager, so the value never appears in a command line. It is
  never written to a file, an image, a seed, stack state, or a log.
- `api_key_helper` declares a helper command in the per-run settings and passes
  no value at all.

An absolute path to a credential file is refused in the configuration outright,
and a configuration key whose name means "secret" is refused with it.

One limit is worth stating rather than glossing. On Distrobox a forwarded value
never appears in a command line: the container manager is given the variable's
*name* and reads the value from the runner's own environment. On the machine
backend the transport is ssh, and the value is placed on the guest command line,
so it is visible in the guest's process table for the life of that command. It
is redacted from everything the runner captures, and the guest is destroyed
after the run, but that is a bounded exposure and not an absence of one.

## Acceptance

Each rail below is proved by an instrument that rejects an implementation which
is present, runs, and returns a pass — not one that is merely absent.
`python3 counterexamples.py` applies each wrong implementation in turn and
checks that its instrument goes red; `python3 counterexamples.py --list` prints
the table with the tests each row runs. The recorded sweep is in
`evidence/counterexamples.txt`.

| Requirement | Instrument | Evidence |
| --- | --- | --- |
| A probe result equal to the host's own snapshot is rejected | `counterexamples.py` case `no-host-fallback` | `evidence/counterexamples.txt` |
| An orca resolving to the host's installation does not count as present | case `orca-is-the-environments-own` | `evidence/run-blocked-on-host-orca/` |
| Cleanup runs only after a confirmed export | case `export-before-destroy` | `evidence/counterexamples.txt` |
| Cleanup runs only after a confirmed stop | case `stop-must-be-confirmed` | `evidence/counterexamples.txt` |
| Evidence is collected and exported before anything is destroyed | case `destroy-after-export` | `evidence/counterexamples.txt` |
| The receipt proves bytes on the host, not bytes in memory | case `receipt-is-a-re-read` | `evidence/run-blocked-on-host-orca/export-receipt.json` |
| A per-run path containing a shared resource is refused | case `cleanup-only-per-run` | `evidence/counterexamples.txt` |
| Redaction catches a credential this run has never seen | case `redaction-by-shape` | `evidence/counterexamples.txt` |
| The manifest carries every required field on every path | case `manifest-required-fields` | `evidence/run-blocked-on-host-orca/manifest.json` |
| The run identifier discloses neither the host name nor the auth reference | case `run-id-carries-no-name` | `evidence/counterexamples.txt` |
| A secret-shaped value under a dull key is refused in the configuration | case `config-refuses-a-secret` | `evidence/counterexamples.txt` |
| No credential value, length or prefix reaches a receipt | case `auth-leaves-no-material` | `evidence/run-blocked-on-host-orca/auth-receipt.json` |
| An unacknowledged host-home mount blocks the run | case `host-home-mount-acknowledged` | `evidence/preflight-distrobox.txt` |
| Preflight inspects without creating the run's state | case `preflight-leaves-no-residue` | `evidence/counterexamples.txt` |
| The preserved base image is never the overlay's output path | case `base-is-only-a-backing-file`, and a real `qemu-img` backing-chain test | `tests/test_adapter_vm.py` |
| A create that fails partway records what it may have left behind | case `failed-create-names-its-residue` | `evidence/counterexamples.txt` |
| A value forwarded into the guest is redacted from captured output | case `forwarded-value-is-redacted` | `evidence/counterexamples.txt` |
| An unverified provider schema blocks the machine backend | case `provider-schema-verified` | `evidence/preflight-vm.txt` |
| Preflight blocks rather than inventing a path | `./run-cycle preflight` on a host with no `pulumi` and no tool image | `evidence/preflight-vm.txt` |
| A real cycle stops at the identity gate rather than using the host | `./run-cycle run` on this host | `evidence/run-blocked-on-host-orca/manifest.json` |
| The rendered program names only classes and fields the pinned provider has | `tests/test_adapter_vm.py::ProviderSchemaIntegrationTest` against the real provider | `evidence/preflight-vm.txt` |
| A hosted state backend blocks the machine backend | case `state-backend-is-local` | `evidence/counterexamples.txt` |
| A graphics type this emulator lacks blocks the run | case `graphics-type-is-supported` | `evidence/preflight-vm.txt` |
| An argument vector survives the transport intact | case `remote-command-is-quoted` | `evidence/counterexamples.txt` |
| Creation waits for the guest to answer, not just to take an address | case `an-address-is-not-readiness` | `evidence/counterexamples.txt` |
| A real machine cycle creates a domain, proves a distinct kernel, and destroys it | `./run-cycle run` on this host | `evidence/vm-blocked-on-absent-orca/manifest.json` |
| First-run state is keyed to the project copy the agent will actually open | case `first-run-state-names-the-environment-copy` | `evidence/distrobox-settled-cycle/first-run-state.json` |
| A receipt that names four answered questions answers four | case `first-run-answers-every-question-it-names` | `evidence/distrobox-settled-cycle/first-run-state.json` |
| A settling message is read from the delivery, not from the request envelope | case `a-settling-message-is-read-from-the-delivery` | `evidence/distrobox-settled-cycle/dispatch-completion-wait.json` |
| The reported outcome is the worker's own verdict, not the message type | case `the-outcome-is-the-workers-own-verdict` | `evidence/distrobox-settled-cycle/dispatch-completion-wait.json` |
| The reply that decided the run is exported beside the verdict | case `the-reply-that-decided-the-run-is-kept` | `evidence/distrobox-settled-cycle/dispatch-worker-start.json` |
| A dispatch the plane could not verify keeps what its agent's terminal held | case `an-unverifiable-dispatch-keeps-its-terminal` | `evidence/vm-unobserved-turn/` |
| A worker really does the task and an independent check agrees | `./run-cycle run` on this host | `evidence/distrobox-settled-cycle/` |
| A coordinator terminal has to say which machine it is on | case `the-terminal-says-which-machine-it-is-on` | `evidence/distrobox-settled-cycle/` |
| A run whose processes are still on the host is residue, not destroyed | case `a-process-left-running-is-residue` | `evidence/counterexamples.txt` |
| The survey matches this run's paths, never a program name | case `the-survey-matches-a-path-not-a-program` | `evidence/counterexamples.txt` |
| Two runners starting together do not both get the slot | case `the-count-is-taken-under-a-lock` | `evidence/counterexamples.txt` |
| A lease whose owner is gone still occupies its slot | case `an-orphaned-lease-still-occupies-its-slot` | `evidence/counterexamples.txt` |
| A run that ended in residue blocks the next one | case `an-unconfirmed-cleanup-keeps-the-slot` | `evidence/counterexamples.txt` |
| The budget sums across the runs already holding a slot | case `the-budget-counts-what-is-already-running` | `evidence/counterexamples.txt` |
| Only the switch raises the ceiling, never a number alone | case `the-switch-is-what-raises-the-ceiling` | `evidence/counterexamples.txt` |
| The host registry is searched for this run, and searching for nothing proves nothing | case `the-registry-is-searched-for-this-run` | `evidence/counterexamples.txt` |
| A registry file that cannot be read is not a clean one | case `a-registry-that-cannot-be-read-is-not-clean` | `evidence/counterexamples.txt` |
| A terminal transcript is not a registration | case `scrollback-is-not-a-registration` | `evidence/counterexamples.txt` |
| An entry naming the run in the operator's registry is residue | case `a-registry-entry-naming-the-run-is-residue` | `evidence/counterexamples.txt` |
| A named skill is not a present skill | case `a-named-skill-is-not-a-present-skill` | `evidence/counterexamples.txt` |
| A skill is pinned by its bytes, not by its name | case `a-skill-is-pinned-by-its-bytes` | `evidence/counterexamples.txt` |
| A probe the environment never answered is not a pass | case `an-unanswered-skill-is-not-a-present-one` | `evidence/counterexamples.txt` |
| A missing pinned skill blocks the run before the dispatch | case `a-missing-skill-blocks-the-run` | `evidence/counterexamples.txt` |
| An image nothing pins is not a verified image | case `an-unpinned-image-is-not-a-verified-one` | `evidence/counterexamples.txt` |
| The shipped configurations run as they stand | case `the-examples-carry-no-placeholders` | `evidence/counterexamples.txt` |
| The container example installs what its own configuration requires | case `the-container-example-installs-what-it-requires` | `evidence/counterexamples.txt` |
| One dispatch answering twice is not two agents | case `two-panes-are-not-two-agents` | `evidence/counterexamples.txt` |
| Identifiers the plane never gave do not establish separateness | case `an-unnamed-dispatch-is-not-a-separate-one` | `evidence/counterexamples.txt` |
| The reviewer's verdict is tied to the diff it was handed | case `the-reviewer-answered-about-this-diff` | `evidence/counterexamples.txt` |
| A diff that moved under the review invalidates the verdict | case `the-diff-did-not-move-under-the-review` | `evidence/counterexamples.txt` |
| The captured diff includes the file the task creates | case `the-capture-includes-a-file-that-is-new` | `evidence/counterexamples.txt` |
| The operator's compositor is not reachable by default | case `the-operators-compositor-is-not-reachable-by-default` | `evidence/counterexamples.txt` |
| The runtime directory is the environment's own, not the operator's | case `the-runtime-directory-is-the-environments-own` | `evidence/counterexamples.txt` |
| A window on this run's own screen is what says where the application went | case `a-window-on-this-screen-is-the-evidence` | `evidence/counterexamples.txt` |
| A screen that did not answer establishes nothing | case `an-unreachable-screen-says-nothing` | `evidence/counterexamples.txt` |
| A checkout at the pinned commit is not a skill the agent can read | case `a-checkout-is-not-a-skill-the-agent-can-read` | `evidence/tool-image-with-skills/` |
| The installed skills are the pinned bytes, not just the right names | case `the-installed-skills-are-the-pinned-bytes` | `evidence/tool-image-with-skills/` |
| A branch is found where a clone actually keeps it | case `a-branch-is-found-where-a-clone-keeps-it` | `evidence/counterexamples.txt` |
| A session variable leaks by where its value points, not by its name | case `a-variable-leaks-by-where-it-points` | `evidence/counterexamples.txt` |
| The plumbing that launched the environment is not the environment | case `the-launcher-is-not-the-environment` | `evidence/counterexamples.txt` |
| The box is created without the operator's session in its environment | case `the-box-is-created-without-the-operators-session` | `evidence/counterexamples.txt` |
| A copied credential is not a working login | case `a-copied-file-is-not-a-working-login` | `evidence/replayed-delivery/manifest.json` |
| An expired login blocks rather than passes | case `an-expired-login-blocks-rather-than-passes` | `evidence/counterexamples.txt` |
| The auth receipt names no person and no organisation | case `the-receipt-names-no-person` | `evidence/counterexamples.txt` |
| The reviewer waits on its own message, not the one before it | case `the-reviewer-waits-on-its-own-message` | `evidence/replayed-delivery/` |

## What no instrument here observes

A second, different task. A run on any host but this one. Any measurement that
would need repetition: start-up time, cost, or how often anything succeeds.

**Isolation on the container backend.** Distrobox mounts the operator's home,
shares their process table, and mounts both `/run/user/<uid>` and `/tmp` — so
their compositor socket and their X sockets are reachable from inside the box
by anything that goes looking for them. What the runner does is narrower and it
is worth stating exactly: it removes the variables that would point a toolkit
at them, gives the environment a runtime directory of its own, and then checks
where the application's window actually went. That is mitigation and a
measurement, not a sandbox, and the preflight blocks until the home mount is
acknowledged as a deliberate compromise.

**That a process is not pointed at the operator's session.** Finding one that
is settles the question; not finding one settles nothing, because the
application rewrites its own environment block to set its process title and
`/proc/<pid>/environ` then reads empty. The window check is what carries the
claim; this only ever contradicts it.

**That an agent read what it was given.** The handoff shows the reviewer was
handed the implementer's diff, that the diff did not change underneath it, and
that the reviewer named that diff in its verdict. Whether it read every line is
not observable here and is not claimed.

**That a skill was loaded or obeyed in a session.** The runner establishes that
each pinned skill is present in the environment, where an agent looks, and is
the pinned bytes. Loading and obedience are further questions and neither is
answered.
