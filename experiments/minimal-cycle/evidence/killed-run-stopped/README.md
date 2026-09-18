# A runner killed mid-flight, and the command that ends what it left

Two runs on this host were killed with `SIGKILL`, on purpose, at two different
moments, and then stopped with `run-cycle stop`. Nothing here was written by
hand; the three substitutions the evidence directory's own README describes are
the only edits.

Until this, the rail for what a killed run leaves had never been fired at a
real killed run: the three private keys that prompted `residue` were found by
looking, and by then their domains were gone. The `README` said so out loud
under "What none of this shows". It no longer does.

## What was measured

A run was taken to its task phase — box up, Orca up, two agents running inside
it, the host home mounted into the box as this backend always mounts it — and
its runner was killed. Six minutes of that had been watched before, on an
earlier day, with nothing coming to stop it. `residue` found all of it and
could only describe it, which is the file below.

`run-cycle stop --run-id <run>` then attributed twenty processes to that run,
one by one and each with the evidence that placed it, stopped every one of
them, confirmed they were gone, and removed the box. It left the per-run state
standing, because what that state holds is `residue --discard`'s question, and
on this backend it holds a copy of the operator's own login.

## The files

### `refusal-lease-has-a-live-owner.txt`

The same command, against the same run, forty seconds earlier — while it was
still alive. It refused, and surveyed nothing. A dead run is established from
its lease, never assumed from the fact that somebody typed its name.

### `residue-could-only-describe-it.txt`

The gap, as it stood. Every process is found and named with what ties it to
the run — a home, a working directory, a command line, or the box's own mount
namespace — and the report ends `this run is not established to be over;
nothing is offered`. Both `claude --dangerously-skip-permissions` processes
are in it. So is the container. Nothing here could end any of it.

Two things it does not contain are the point: the operator's own Orca, and the
operator's own containers. The container manager runs its client and its
monitor in one mount namespace shared by every box this user has, and those
processes carry this run's paths on their command lines, because the paths are
what they were told to mount. A namespace is therefore established as this
run's only from a process's own home or working directory — where it *is*,
rather than what it was told — and that is what kept the operator's
`claude-desktop` box out of this list.

### `stop-what-the-killed-run-left.txt`

Twenty processes attributed, every one with its reason, all twenty stopped,
the box removed, the state left standing. Afterwards the operator's Orca was
still running with all twelve of its processes, their own agent was still
running, and a second run's process, started deliberately beside this one with
that other run's per-run home, was untouched.

### `refusal-a-process-it-could-not-place.txt`

The refusal that matters more than the success, on a second run killed early —
while the box was still installing its own packages as its own root, which
this host maps to a subordinate identifier the operator cannot read the
environment of.

Those processes cannot be shown to be this run's and cannot be shown not to
be. They are named, with what could not be read about them, and nothing at all
is signalled: not them, and not the nineteen processes the same survey *had*
placed. Stopping those and leaving this one unmentioned would be the worst of
both — it acts, and it reports a host it established nothing about.

The file carries the whole sequence, including the part that is a limit rather
than a success. When the package install finished, one unreadable process
remained: the box's own keepalive loop, a child of its own root-owned init,
which never exits while the box is up. For a run killed that early — before
anything of the invoking identity had run inside its box — there is no home
and no working directory to establish the box's namespace from, so that
process can never be placed and this command can never remove that box. The
container manager is what reaps it, and the file shows the operator doing
that and the command then settling.

What would close that window is a third source of attribution: the container
manager knows which host process is that box's init, and `processes.attribute`
already takes recorded process identifiers as roots. It is not wired up here,
because it answers the question from a different authority than the three the
rest of this uses, and that is a decision to take deliberately rather than
late.

### `then-the-state-and-the-slot.txt`

`residue --discard` and `release`, in that order, for the first run. The
receipt names the copy of the operator's own login that went with it.

### `host-afterwards.txt`

The host read back item by item: containers, domains, the state root, and
every file left under it read by its bytes rather than by its name. Zero
private keys, zero credential values. The operator's three containers are
untouched, and the only windows on their display are their own.

## What this does not show

A run killed on the machine backend. A guest has a process table of its own,
so a process identifier from inside it means nothing here, and what a domain
left running is a different question with a different answer.

A process this survey cannot see at all. A box that shares the host's process
table gives an orphaned process inside it a parent outside the run, and an
unreadable one whose parent has already exited appears in no list here.
Removing the box is what reaps that one, which is why the command that removes
the box is the one that surveys.

That the second run's process used for the untouched check was a second
container. The admission ceiling on this host is one environment, so it was a
real process carrying a second run's per-run home and working directory — the
discrimination under test — and not a second box.
