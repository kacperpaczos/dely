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
./run-cycle residue       --config <config>   # what a run that never finished left
./run-cycle stop          --config <config> --run-id <id>
./run-cycle residue       --config <config> --run-id <id> --discard
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
prepare -> create -> bootstrap -> identity -> task -> toolchain -> review
        -> check -> collect -> export -> cleanup -> close
```

Two orderings are the point of the whole runner.

**Evidence is exported before anything is destroyed.** `collect` brings the
project tree back to the host, `export` writes every artifact and then re-reads
each one from its host path and recomputes its digest. Only an export where
every required artifact matches releases the run to stop and destroy anything.
An unconfirmed export, or an unconfirmed stop, leaves the environment standing
and records residue with the reason.

**What ran is asked for, not inferred.** `toolchain` comes straight after the
task and before the terminals holding the agents are released, because the
agent's own process is the only thing that can say which Claude Code it was.
The agent is not launched by this runner: the plane's application spawns it,
through whatever search path that application holds. A box inherits the host's
search path, and on a host whose path opens with directories under its own home
that is how a box that installed a pinned Claude Code ran the operator's one
instead. The phase records the binary a bare name selects, the version it
reports compared to the pin, the builds that stayed reachable behind it — the
first of which is what the name resolved to before the ordering was fixed — and
what this run's own processes were executing. A process that had already exited
is recorded as unread rather than as a pass.

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
what was pinned, or landed where the agent never looks, blocks the run. The
install itself names a commit, in both backends: `npx skills add <repo>` — which
is what `orca skills install` resolves to — takes no revision, so it installs
whatever upstream's tip is that day and agrees with the pinned digests only by
coincidence. It stopped agreeing, and the gate caught it.

**The environment must not register itself into the operator's own Orca.** The
host's registry is searched for this run's identifier, container or domain and
per-run paths — as bytes, so a repository entry, a worktree, the record that a
terminal exists and a row in the orchestration store are all caught the same
way. An entry naming the run makes the cleanup residue. Nothing here removes
it: it is in somebody's own profile.

**A run that never finished is still running.** Cleanup is the last phase, so
a run killed in the middle of one never reaches it and nothing stops what it
started. Measured here, not supposed: a runner killed with `SIGKILL` in its
task phase left a box up, an agent inside it with the host home mounted, and a
whole application runtime, and six minutes later they were all still going.
`residue` found them and could only describe them — it refuses to remove state
while processes hold it, which is right, and there was no way to end them.
`run-cycle stop --run-id <id>` is that way.

It acts on one run the operator names, and on nothing else. It refuses while
that run's lease still has a live owner, because a dead run is established and
never assumed. Every process it signals is one this run's own paths, or this
run's own mount namespace, put there — never a program name, which would reach
the operator's own application. A process this host will not let it read is
neither signalled nor skipped: it is named, and the command refuses over it,
the same way `residue` refuses over a question this host could not put. A
signal the host refused is reported as a process that did not stop rather than
as one that did. Only when everything is gone does the box or the domain go,
and the per-run state stays standing for `residue --discard`, which is where
the questions about what that state holds are asked.

**A run that never finished still left something.** Cleanup is the last phase,
so it only ever runs for a run that reached the end. A killed or interrupted
one leaves its whole per-run state on the host, and on the machine backend that
state is a private transport key. Three were found that way, for domains that
no longer existed. The admission lease already survives a dead run and already
knows its owner is gone; it now also says what that run left on disk, and names
a private key among it as one. `run-cycle residue` is the same report for the
host as a whole, including for a run whose lease somebody has already cleared,
and `--discard` removes one named run's state — never before its processes, its
container or domain, and its lease have each been asked, and never on a
question this host could not put at all. Key material is named from its bytes,
so a key under a dull name is still named and a public half under `id_cycle` is
not.

**A settled Task is not a released terminal.** A valid `worker_done` settles the
Task and the Dispatch on its own and leaves the terminal the plane opened for
that agent live — terminal state is separate accounting, and Orca's own command
line says so. After each report the coordinator owes exactly one decision:
reuse, retain, or release. This runner releases, for both agents; reuse is the
one option the review cannot take, because a reviewer inheriting the
implementer's session inherits its context and the independence the handoff
exists to establish would be gone. The plane is asked who is owed rather than
told, each row it names is released by its dispatch, and the same question is
asked again afterwards: an answer that is not empty is a coordinator ending its
turn in debt, and it costs the run its status.

Two orderings around that are worth stating. Both releases wait for the picture
taken at the end of the review, because that is the one moment at which both
agents' terminals are alive at once — a deliberate deviation from disposing
immediately, and the debt is still paid inside the same turn. And the terminals
are listed either side of the release, because a released terminal drops out of
`orca terminal list` and a terminal whose panel is merely off screen does not.
That comparison is the only thing here that tells the two apart.

**A live terminal is not a drawn panel.** Closing the application's own
first-run questions cleared the frame and uncovered a second cause underneath:
the run selects no workspace, and workspace selection is what mounts a terminal
panel. Every terminal was live, the status bar counted them and the sidebar
listed the project and its worktree, while the centre of the frame was the empty
state asking somebody to pick a workspace. So before that picture the run asks
the application for each agent's panel by the handle that agent's own dispatch
returned — `orca terminal switch`, which selects that terminal's workspace and
then activates its tab. The application foregrounds one tab at a time, so the
last switch names the panel the image holds and the other is its neighbour in
the tab strip. What the receipt answered is exported beside the image, because a
switch can exit zero having moved nothing, and an unasked frame and a refused
one look identical.

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
  first-run-state.json       the questions this fresh home was answered, by
                             name, the application's own among them
  admission.json             the slot this run held, and what else held one
  skills.json                each required skill, where it was and what it hashed to
  screenshot.json            each picture of this run's own screen, what it cost,
                             and which agent panels were asked for before the last one
  screenshots/               the images themselves, one per moment
  review.json                the handoff: the diff, the two agents, the verdict
  handoff-diff.patch         the diff the reviewer was given
  terminals.json             what each agent's terminal was owed, what was done
                             about it, and the live list either side
  host-registry-before.json  the operator's own Orca registry, before
  host-registry-after.json   and after, with the question it was searched for
  bootstrap/                 what each provisioning and repository step printed,
                             one pair of streams per step, numbered in the
                             order they ran
  dispatch/                  each orchestration reply, redacted, as the plane sent it
  host-before.json           the host before the run
  host-after.json            the host after it, and what changed
  identity/host-probe.txt    the probe as the host answered it
  identity/environment-probe.txt  the probe as the environment answered it
  identity/orca-status.json  what Orca reported inside the environment
  toolchain.json             which Claude Code a bare name selects in the
                             environment, its version against the pin, the
                             builds still reachable behind it, and what this
                             run's own processes were running
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

A captured command's stream is bounded as well as redacted. One `apt-get`
install prints thousands of lines, so the last 64 kilobytes are kept and a
stream that did not fit says so in its own first line, with the count of bytes
dropped: a truncated log that reads as a complete one is worse than no log,
because somebody draws a conclusion from whichever part survived. Redaction runs
before the bound and never after, because half a shape matches no shape and a
bound taken first would keep the tail of a token as ordinary text.

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
| A receipt that names five answered questions answers five | case `first-run-answers-every-question-it-names` | `evidence/distrobox-settled-cycle/first-run-state.json` |
| The application's own first-run flow is closed, which is the whole of what shows its wizard | case `the-first-run-flow-is-closed-not-merely-recorded` | `python3 counterexamples.py` |
| The profile seed lands before the application is started, which is the only moment it can | case `the-profile-seed-lands-before-the-application-starts` | `python3 counterexamples.py` |
| A settling message is read from the delivery, not from the request envelope | case `a-settling-message-is-read-from-the-delivery` | `evidence/distrobox-settled-cycle/dispatch-completion-wait.json` |
| The reported outcome is the worker's own verdict, not the message type | case `the-outcome-is-the-workers-own-verdict` | `evidence/distrobox-settled-cycle/dispatch-completion-wait.json` |
| The reply that decided the run is exported beside the verdict | case `the-reply-that-decided-the-run-is-kept` | `evidence/distrobox-settled-cycle/dispatch-worker-start.json` |
| A dispatch the plane could not verify keeps what its agent's terminal held | case `an-unverifiable-dispatch-keeps-its-terminal` | `evidence/vm-unobserved-turn/` |
| A worker really does the task and an independent check agrees | `./run-cycle run` on this host | `evidence/distrobox-settled-cycle/` |
| A coordinator terminal has to say which machine it is on | case `the-terminal-says-which-machine-it-is-on` | `evidence/distrobox-settled-cycle/` |
| A run whose processes are still on the host is residue, not destroyed | case `a-process-left-running-is-residue` | `evidence/counterexamples.txt` |
| The survey matches this run's paths, never a program name | case `the-survey-matches-a-path-not-a-program` | `evidence/counterexamples.txt` |
| A process this host will not let the survey read is named, not skipped | case `a-process-this-survey-cannot-read-is-not-a-no-match` | `evidence/killed-run-stopped/` |
| Nothing is signalled at all while one process near the run cannot be placed | case `nothing-is-signalled-while-a-process-cannot-be-placed` | `evidence/killed-run-stopped/` |
| A signal the host refused is not a stop | case `a-signal-the-host-refused-is-not-a-stop` | `evidence/counterexamples.txt` |
| The environment's namespace is learned from where a process is, not from its argv | case `the-environments-namespace-is-learned-from-where-a-process-is` | `evidence/killed-run-stopped/` |
| A run whose lease still has a live owner is not a dead run | case `a-dead-run-is-established-and-never-assumed` | `evidence/killed-run-stopped/` |
| The box goes only after the processes it held are gone | case `the-environment-goes-only-after-its-processes-do` | `evidence/killed-run-stopped/` |
| Stopping a dead run does not take what it left on disk | case `stopping-a-dead-run-does-not-take-what-it-left-on-disk` | `evidence/killed-run-stopped/` |
| A runner killed mid-flight leaves a box and an agent, and one command ends them | `./run-cycle run`, `SIGKILL`, `./run-cycle stop` on this host | `evidence/killed-run-stopped/` |
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
| A capture is never pointed at the operator's own screen | case `a-capture-is-never-pointed-at-the-operators-screen` | `python3 counterexamples.py` |
| An image that never reached the host is not a picture | case `an-image-that-never-reached-the-host-is-not-a-picture` | `python3 counterexamples.py` |
| A capture that failed does not fail the run | case `a-failed-capture-is-not-a-failed-run` | `python3 counterexamples.py` |
| A live terminal is not a drawn panel; the panels are asked for by name | case `a-live-terminal-is-not-a-drawn-panel` | `python3 counterexamples.py` |
| A switch that exited zero is not a window that moved | case `a-switch-that-exits-zero-is-not-a-window-that-moved` | `python3 counterexamples.py` |
| A checkout at the pinned commit is not a skill the agent can read | case `a-checkout-is-not-a-skill-the-agent-can-read` | `evidence/tool-image-with-skills/` |
| The installed skills are the pinned bytes, not just the right names | case `the-installed-skills-are-the-pinned-bytes` | `evidence/tool-image-with-skills/` |
| An install that names no revision cannot be pinned | case `an-install-that-names-no-revision-is-not-a-pin` | `python3 counterexamples.py` |
| A built image freezes whichever tip it was built on | case `an-image-freezes-whichever-tip-it-was-built-on` | `python3 counterexamples.py` |
| A branch is found where a clone actually keeps it | case `a-branch-is-found-where-a-clone-keeps-it` | `evidence/counterexamples.txt` |
| A full cycle completes on the container backend, review and all | `./run-cycle run` on this host | `evidence/distrobox-reviewed-cycle/` |
| An agent's panel is drawn in the window, not merely a terminal the runtime owns | `./run-cycle run` on this host | `evidence/distrobox-agent-panel/` |
| A full cycle completes on the machine backend | `./run-cycle run` on this host | `evidence/vm-reviewed-cycle/` |
| The guest's processor is the reason, tested both ways | the same run with `cpu_mode: default` | `evidence/vm-processor-control/` |
| A session variable leaks by where its value points, not by its name | case `a-variable-leaks-by-where-it-points` | `evidence/counterexamples.txt` |
| The plumbing that launched the environment is not the environment | case `the-launcher-is-not-the-environment` | `evidence/counterexamples.txt` |
| The box is created without the operator's session in its environment | case `the-box-is-created-without-the-operators-session` | `evidence/counterexamples.txt` |
| A copied credential is not a working login | case `a-copied-file-is-not-a-working-login` | `evidence/replayed-delivery/manifest.json` |
| An expired login blocks rather than passes | case `an-expired-login-blocks-rather-than-passes` | `evidence/counterexamples.txt` |
| The auth receipt names no person and no organisation | case `the-receipt-names-no-person` | `evidence/counterexamples.txt` |
| The reviewer waits on its own message, not the one before it | case `the-reviewer-waits-on-its-own-message` | `evidence/replayed-delivery/` |
| A coordinator that ends its turn still owing terminals is not a settled run | case `a-coordinator-that-ends-its-turn-still-owing-terminals` | `python3 counterexamples.py` |
| The answer the plane gives to say it does not know is not a release | case `an-unverified-release-is-not-a-released-terminal` | `python3 counterexamples.py` |
| A release receipt is not a closed terminal; the live list is | case `a-release-receipt-is-not-a-closed-terminal` | `python3 counterexamples.py` |
| Two environments hold slots at once and the ceiling refuses the next | `./run-cycle run` with `limits.parallel: true` on this host | `evidence/distrobox-parallel/` |
| The budget refuses a claim that overruns it across the live leases, before anything is created | `./run-cycle run` with `limits.parallel: true` on this host | `evidence/distrobox-parallel/` |
| A provisioning step that failed leaves what it printed, not just its exit code | case `a-provisioning-step-keeps-what-it-printed` | `python3 counterexamples.py` |
| A bounded log says it was bounded, and by how much | case `a-bounded-log-says-it-was-bounded` | `python3 counterexamples.py` |
| A value this run forwarded is removed from a stream it has no shape in | case `a-stream-is-redacted-with-this-runs-own-values` | `python3 counterexamples.py` |
| A credential lying across the bound is not kept as its tail | case `a-stream-is-redacted-before-it-is-bounded` | `python3 counterexamples.py` |
| A key left by a dead run is named from its bytes, not from its name | case `the-key-a-dead-run-left-is-named-by-its-bytes` | `python3 counterexamples.py` |
| A lease with no live owner names what that run left on disk | case `a-lease-with-no-live-owner-says-what-its-run-left` | `python3 counterexamples.py` |
| Nothing of a run is discarded while anything says it may be alive | case `nothing-is-discarded-while-the-run-may-be-alive` | `python3 counterexamples.py` |
| A question this host could not ask is not an answer of no | case `a-question-this-host-could-not-ask-is-not-an-answer` | `python3 counterexamples.py` |

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

**A picture is not a proof.** Each run also exports an image of its own screen,
taken from outside the application — through the hypervisor on the machine
backend, and through the X server drawing the window on the container backend,
never by asking the application to photograph itself. It is there so a reader
can see the two agents' terminals rather than their identifiers. Nothing rests
on it: a capture that fails is recorded with its reason and costs the run
nothing, and the window check next to it is what carries the claim about where
the application went. The operator's own screen is never captured, in any mode.

**That a process is not pointed at the operator's session.** Finding one that
is settles the question; not finding one settles nothing, because the
application rewrites its own environment block to set its process title and
`/proc/<pid>/environ` then reads empty. The window check is what carries the
claim; this only ever contradicts it.

**That a `release_pending` ever completes.** Closing a terminal itself is no
longer a fake's answer. Live runs have released both of their agents'
terminals: the plane answered `released`, `worker-list` moved each dispatch
from `reclaimable` to `released`, and the live list went from three terminals
to the coordinator alone — which is a closure and not a disappearance, because
a terminal whose panel is merely off screen stays in that list. What no run has
produced is the other outcome. Every live release so far answered outright, so
the `release_pending` branch, and whether such a release has completed by the
time the debt is asked about again, are still read from the command's own help
and exercised only against a fake that answers as it describes.

**That a real credential ever passed through a provisioning log.** A stream
artifact is redacted before it is bounded, and both halves of that are exercised
against stand-ins: a shaped token, a value with no shape that only the literal
removes, and one lying across the cut. No run has put a real credential into a
provisioning step's output, and as the bootstrap is ordered none could —
provisioning runs before any auth material is placed, so the list of forwarded
values is empty while it runs. What is established is that the artifact would
remove one; that a live run produced one to remove is not.

**Both agents' panels in one frame.** The application foregrounds one terminal
tab at a time, so the picture holds the panel the last switch named and the
other agent's tab beside it in the tab strip. Nothing on the command line moves
a live terminal into another tab's layout, and the run does not fake one: it
asks for each panel, records what the window answered for each, and photographs
whichever it ended on. `evidence/distrobox-agent-panel/` is the frame that
holds one of them, and says which.

**Which profile a cold start selects.** The seed is written to
`local-default`, because that is the profile the bundle's own factory mints.
Whether the index beside the profiles must already exist for that profile to be
chosen was not established, and nothing here writes one: the onboarding value's
shape was read, the index's was not, and a guessed index that selects the wrong
profile would be a worse failure than the wizard and a quieter one. If a capture
still shows the wizard with the seed on disk, that index is what to read next.

**That an agent read what it was given.** The handoff shows the reviewer was
handed the implementer's diff, that the diff did not change underneath it, and
that the reviewer named that diff in its verdict. Whether it read every line is
not observable here and is not claimed.

**What a run interrupted at a moment nobody has tried leaves.** Two runs have
now been killed here on purpose — one in its task phase with a box, an
application and two agents running, one while its box was still installing its
own packages — and `evidence/killed-run-stopped/` is what the rails found and
ended. A half-created domain, an overlay with no domain, and a lease still held
by a process that is wedged rather than dead are still not observed, and the
report would say of each only what its own three questions can establish.

**A run killed on the machine backend.** A guest has a process table of its
own, so an identifier from inside it means nothing on this host, and what a
domain left running is a different question. `stop` removes the domain, the
overlay and the seed there and leaves the state for `--discard`; that path is
covered by its tests and by nothing that was killed.

**A process inside the box that this survey cannot see at all.** A box sharing
the host's process table gives an orphaned process inside it a parent outside
the run, so an unreadable process whose parent has exited appears in no list.
Removing the box reaps it, which is why the command that removes the box is the
one that surveys — but the survey does not claim to have named it.

**That a skill was loaded or obeyed in a session.** The runner establishes that
each pinned skill is present in the environment, where an agent looks, and is
the pinned bytes. Loading and obedience are further questions and neither is
answered.
