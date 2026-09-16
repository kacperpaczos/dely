# The machine backend, not completing

Same commit, same host, same task as `../distrobox-settled-cycle/`. The
container settled; this did not.

    status:    TIMEOUT
    identity:  ENVIRONMENT   distinct machine identity, boot identity, hostname, virt=kvm
    orca:      runtime ready, window available
    dispatch:  made, then `outcome_unknown` / `turn_start_unobserved`
    check:     SKIPPED — nothing to check
    export:    CONFIRMED
    cleanup:   DESTROYED

## How far it got

Everything up to the worker. Pulumi declared the overlay, seed and domain over
a base image pinned by digest; the guest booted with a kernel of its own;
`first-run-state.json` shows the agent's four first-run questions answered in
the guest's home; Orca reported `ready` with a window; a coordinator terminal
opened, a Run was created and a Dispatch was made with the pinned model and
effort.

## Where it stops, in the agent's own terminal

`dispatch-worker-terminal.txt` is the screen the plane pointed at, captured by
the run itself:

    cycle@…:~/project$ claude '--dangerously-skip-permissions' '--model' 'claude-opus-5' '--effort' 'high'

and nothing after it, at five, twenty and forty seconds.

## What was ruled out, and what was not

Ruled out by direct measurement in the guest during a separate session:

- the network. DNS resolves `api.anthropic.com` and an HTTPS request completes.
- the shell. A plain terminal runs what is sent to it, and a terminal created
  with a startup command runs that too — so the plane does submit here.
- the missing Enter. Sending one more Enter into that terminal changed nothing
  at all, which a standing unsubmitted line would not do.
- the first-run questions. They are answered in this guest, and the same
  answers are what let the container backend through.

Not ruled out: the agent itself. `claude -p` in this guest produced no output
before its deadline, where the same command with the same token answers on the
host in about a second. That measurement was interrupted before it returned an
exit code, so it is a lead and not a finding.

## What it turned out to be

That lead was the right one, and the reason it could not be closed here is that
it was being watched from too far away. Every observation on this page holds;
none of them could see that *one command was not returning*.

What found it was asking the agent a question it had to answer — whether it was
signed in — and, when it did not, looking at the process rather than guessing:
running rather than waiting, no descendants, no open sockets. The domain had
never said anything about its processor, so the emulator had been giving it
`QEMU Virtual CPU version 2.5+`, whose flags stop at `sse2`.

`../vm-reviewed-cycle/` is the same backend completing once the guest is given
this machine's processor; `../vm-processor-control/` puts it back and shows it
blocking again.

## What this does not show

That the machine backend cannot work, or that the agent is at fault. It shows
one guest, on one host, where the agent was launched and never spoke — and it
is kept because the same failure looks like this from a distance.
