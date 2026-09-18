# Two boxes at once, and two refusals that are not the same refusal

The first time `limits.parallel: true` was exercised against real environments
rather than against the counterexample suite. Four runs started within 33
seconds of each other on one host, against `max_active: 2` and a budget of 6144
megabytes of memory.

| run | claim | outcome |
| --- | --- | --- |
| `20260918T092735Z-bbc5e6-a5defabd` | 3072 MB | `SETTLED` in 235.719 s; held a slot, released it |
| `20260918T092748Z-bbc5e6-7a14796a` | 4096 MB | `BLOCKED` in 0.006 s, refused by the budget |
| `20260918T092756Z-bbc5e6-7f96304f` | 3072 MB | `BLOCKED` in 167.688 s; held the second slot, released it |
| `20260918T092808Z-bbc5e6-1893b4bb` | 3072 MB | `BLOCKED` in 0.035 s, refused by the ceiling |

## Two slots were held at the same instant

The fourth run's refusal names both holders:

> the distrobox backend allows 2 active environment(s) and 2 slot(s) are
> occupied: 20260918T092735Z-bbc5e6-a5defabd is running as pid 1394831;
> 20260918T092756Z-bbc5e6-7f96304f is running as pid 1397464

That sentence is the evidence, and what makes it evidence is the wording "is
running as". A lease is only described that way when it is `held`, and it is
only classified `held` when the recorded pid is read out of the process table
and its start time still matches the one the lease recorded. A lease file left
by a dead owner reads as orphaned and gets a different sentence. So at 09:28:08
both runs' processes were alive, 33 and 12 seconds after they took their slots
at 09:27:35 and 09:27:56.

A second witness, taken by a different mechanism: `host_after` in the same
manifest, captured at 09:28:08, lists both `20260918T092735Z-bbc5e6-a5defabd`
and `20260918T092756Z-bbc5e6-7f96304f` under the state root. That shows two
runs with state on the host at that moment. It says nothing about liveness,
which is what the lease records carry.

The occupant list also carries each holder's claim, 3072 MB apiece, which is
what the budget sums over.

## The two refusals

**The budget**, `budget-refusal-manifest.json`:

> the distrobox backend would be asked for 7168 megabytes of memory against a
> budget of 6144

One run was holding 3072 MB, this one claimed 4096 MB, and 7168 is the sum.
The refusal is arithmetic over the claims of the leases that were live at that
moment, not a reading of the machine, and it took 0.006 seconds. Nothing was
created: every phase from `create` to `collect` is `SKIPPED`, and cleanup
reports "no environment was created, so no per-run resource existed".

**The ceiling**, `ceiling-refusal-manifest.json`: the sentence quoted above,
in 0.035 seconds, likewise before anything was created.

The two are different gates. The ceiling counts occupied slots; the budget adds
resources across them. The ceiling is tested first, so the fourth run's
sentence names the ceiling although its claim also overran the budget:
3072 + 3072 already held, plus 3072 claimed, is 9216 against 6144. Which of the
two answers first is a property of the order in the code, not of this
measurement.

## The second box took its slot and did not provision

This is the part the run count flatters.
`blocked-on-orca-manifest.json`, run `20260918T092756Z-bbc5e6-7f96304f`, held
the second slot for 167.688 seconds and ended `BLOCKED`:

> orca is not present inside the environment; the run stops rather than using
> the host's installation

What the artifacts show, in order. The box was created: `create` ended `OK`
after 81.953 s. `bootstrap` ran the seven provisioning commands, and the third
of them exited 100 after 13.983 s. That command downloads the `orca-ide`
package, checks its digest, and installs it with `apt-get`.
`blocked-on-orca-runner.log` records it as a bare number:

```
2026-09-18T09:30:02Z ran distrobox in the environment: exit=100 timed_out=False elapsed=13.983s
```

The phase then reported `bootstrap ended as OK`. Nothing in that phase's status
reflects the failed step. The run stopped one phase later, at the identity
gate: the environment probe came back with an empty `orca_path`, and the
follow-up `orca status --json` exited 127.

Everything else in the box was in order, which is what makes this worth
keeping. Node installed, the agent installed, all three pinned skills present
at the pinned revision, the auth material copied and removed and the removal
verified. One package out of that list did not install, and the identity gate
is what noticed.

**Why it did not install is not established.** The exit code is the only thing
the artifacts kept: `stdout_path` and `stderr_path` are `null` for every
bootstrap command in this manifest, as they are in the settled one, so no
output from that step exists to read. 100 is `apt-get`'s own failure code and
the other commands in the step fail with codes of their own, so the install
rather than the download or the digest check is where it stopped, but that is
read off an exit code and nothing more.

The artifacts do not show contention. What they do show is an overlap: the same
step ran in the settled run from 09:29:59 to 09:30:24 and exited 0, and in this
run from 09:29:48 to 09:30:02, so the two were inside that command together for
roughly three seconds. An overlap is not a cause, and nothing in either
manifest connects the two. No memory figure, no disk figure and no message was
captured at the moment of the failure. The artifacts are compatible with
contention and with a package fetch or install that would have failed on its
own, and they do not separate them.

Both slots were released either way. This run's cleanup reports `DESTROYED`,
removing `container:dely-cycle-20260918t092756z-bbc5e6-7f96304f` and its state
path, preserving the shared image, with no process left holding a path of the
run.

## The run that did settle

`settled-manifest.json`: `SETTLED` in 235.719 s, worker at `claude-opus-5` and
effort `high` reporting `worker_done` with outcome `succeeded`, the independent
check observing the marker and exiting 0, four patch lines collected, one
window on the screen the run created, one picture of it, the implementer's
terminal released and only the coordinator's own left live. `DESTROYED`,
nothing left on the host.

Its admission record is the mirror of the refusals:

> this run holds 20260918T092735Z-bbc5e6-a5defabd against a ceiling of 2 active
> distrobox environment(s); the slot was released

## What this does not establish

**That two concurrent boxes provision reliably on this host.** One of the two
did not. At two, one attempt in two failed to install a package inside its box,
and the cause is unknown. Nothing here supports a claim about how often that
happens or why.

**That the rail is safe at any scale.** It was exercised at two, with a ceiling
of two. Nothing was run at three, and the budget refused with exactly one live
claim in its sum, never with two.

**Anything about the handoff.** The review phase was switched off in this
configuration to halve the load, so `review` and `reviewer` are `SKIPPED` in
every one of the four manifests. What two concurrent runs do to a two-agent
cycle is untouched by this.

**Anything about a second task or a second host**, as with everything else in
this directory.
