# What this runner actually did on one host

Everything here was produced by the committed code on one Fedora host with
rootless Podman, Distrobox, `qemu-img`, `virsh` on `qemu:///system`, `/dev/kvm`,
Pulumi 3.262.0 with a local `file://` backend, and a Packer-built tool image.
Nothing here was written by hand.

## What was scrubbed, and what was not

Three substitutions were applied to the files in this directory, and only
these: the host's home directory became `<host-home>`, the host user name
became `<host-user>`, and the raw `machine_id` and `boot_id` lines in the probe
captures became `<scrubbed>`. Digests, sizes, timings, exit codes, statuses and
reasons are untouched. The manifests already store host identifiers as digests
rather than as names.

## Read these first

### `distrobox-settled-cycle/`

The one complete run. A worker read the prompt, wrote the marker byte for byte,
reported `worker_done` with outcome `succeeded`, and an independent check run
outside that session agreed. `SETTLED`, exported, destroyed.
`dispatch-completion-wait.json` carries the worker's own message.

### `replayed-delivery/`

The container backend reaching the handoff and stopping there, with both
dispatches carrying one delivery identifier. A bound Run replays a delivery
until it is acknowledged, so the reviewer's wait woke on the implementer's
message and reported that agent's outcome as its own — a review that never ran,
reporting success. What caught it was the missing verdict artifact, not the
outcome.

### `vm-reviewed-cycle/`

The machine backend completing, `SETTLED` in 153 seconds: a per-run libvirt
domain over an overlay on the preserved tool image, two dispatches with two
agent terminals and two deliveries under one Run, a reviewer that named the
diff it read, and an independent check that agreed. It also carries the path
that found what had been stopping it.

### `vm-processor-control/`

The same configuration with the processor put back the way it was, and nothing
else changed: `BLOCKED` again. The guest's default processor is `QEMU Virtual
CPU version 2.5+`, whose flags stop at `sse2`.

### `vm-unobserved-turn/`

The machine backend before the cause was found: domain created, distinct kernel
proved, Orca ready, coordinator terminal open, dispatch made — and the worker's
turn never observed. Kept because it is the same failure seen from far away,
and because what it ruled out was all correct; what it could not see was that
one command was not returning.

### `tool-image-with-skills/`

The image the machine backend runs, and what went into it: node from a pinned
tarball, Orca and Claude Code at pinned versions and digests, Superpowers at a
pinned commit with all fourteen of its skills, and Orca's own `orchestration`
and `orca-cli` verified against the digests Orca's bundled manifest records.
The build fails if any of those digests disagree.

## What had to be measured before it could be built

### `skill-install-probe/`

What the documented skill install actually does, run in a throwaway container.
Three things came out of it: Ubuntu's node 18 cannot run the skills command
line at all, the agent name `orca skills install` passes is one that command
rejects, and what it does install hashes to exactly what Orca's own manifest
records.

### `plugin-cli-hangs-in-the-guest/`

`claude plugin marketplace add` given ten minutes in the machine backend's
guest, with stdin closed: no output, no return, deadline. The same command
answers in under a second in a container with a terminal attached, without one,
with Orca installed, as root and as an ordinary user; memory and disk in the
guest were fine. Why is not established, and the file says so. Nothing depends
on that command any more.

## Earlier runs, kept because each one moved a rail

### `preflight-distrobox.txt`, `preflight-vm.txt`

Both backends refusing on a host that could not yet run them. The container
finding that matters: Distrobox mounts the host home into every box and has no
flag to suppress it, so a run is blocked until the configuration records that
compromise deliberately.

### `run-blocked-on-host-orca/`

The first real cycle, stopped at the identity gate because the box resolved
`orca` to the *host's* launcher through the mounted host home. Presence of a
command is not presence of an installation. Counterexample
`orca-is-the-environments-own` shows the old rule letting a host-driven run
settle `SETTLED`.

### `vm-blocked-on-absent-orca/`

The first real machine cycle: Pulumi declared the overlay, seed and domain, the
guest booted, answered and settled, and the run stopped because the image
carried no Orca. Three defects came out of running it — the guest agent broke
domain creation, `ssh` flattened the probe's argument vector, and a DHCP lease
is not readiness.

### `orca-in-vm/`, `distrobox-own-orca/`

Orca installed and reporting `ready` inside each backend, with its own runtime
identity rather than the host's.

### `vm-two-sessions/`, `vm-dispatch-unverifiable/`, `vm-timeout/`

Control and a separate coder session proved by identity rather than by layout;
a dispatch the plane could not verify; a deadline firing with every artifact
kept and the environment destroyed.

### `distrobox-full-cycle/`

One container cycle on a display of its own. Its own README corrects a claim it
originally made: the virtual display did **not** keep the application off the
host's desktop.

### `counterexamples.txt`

`python3 counterexamples.py`. Each line replaces one rail with an
implementation that is present, runs and returns a pass, then runs the tests
that are supposed to reject it. `RED` means the instrument rejected the wrong
implementation. A row reporting `GREEN` would be a row proving nothing.

## What none of this shows

A second, different task. A run on any host but this one. Isolation on the
container backend: it mounts the operator's home, shares their process table
and mounts the directories holding their display sockets, and the runner
records that rather than claiming otherwise. And whether the agent that spins
on a processor without those flags would ever finish — the probe meant to
answer that was itself cut short.
