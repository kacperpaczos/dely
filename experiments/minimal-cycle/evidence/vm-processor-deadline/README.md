# The probe with room to finish, and an answer that is still bounded

`../vm-processor-control/` left one thing open: whether `claude auth status`
would ever finish on the processor the emulator picks by itself. The probe meant
to answer that was cut off by the run's own command deadline, so it answered
nothing. The deadline was raised from 180 seconds to 420 afterwards. This is the
first run to use it.

    status:      BLOCKED   the agent did not answer whether it is signed in
    backend:     vm, `cpu_mode: default`, the same configuration as the control
    run:         20260918T091124Z-bbc5e6-f0bd3fa8, 516 s
    diagnosis:   355.791 s against a 420 s deadline, exit 0, `timed_out: false`
    cleanup:     DESTROYED

## The measurement

The probe ran to completion. `manifest.json` records that command finishing in
355.791 seconds against its 420 second deadline, exit 0, `"timed_out": false`.
Nothing inside it was truncated, which is the entire difference from the earlier
attempt.

The line it exists for:

```
== claude auth status with room to finish ==
exit 124 after 240s
```

Four minutes, killed at its own bound, having printed nothing. Where the control
run's file ends with `deadline of 180 seconds reached` and no result, this one
carries a result. It is the first real measurement of that question.

## What it does not settle, and why a longer bound will not

Stuck and unusably slow are still not separated. That is structural, not a
shortcoming of this run: a bounded probe can establish "not by time T" and
nothing more. Both candidates predict exactly the picture this run recorded:

- a loop that does not terminate, and
- real work advancing very slowly on a processor whose flags stop at `sse2`,
  with no `sse4_2`, no `popcnt`, no `aes` and no `avx`.

The rest of the observation does not choose between them either. `STAT Rl`, an
empty wait channel, no descendants, no open sockets: a process that is running
rather than waiting looks the same whether it is repeating itself or getting
somewhere.

Separating them needs an instrument of a different shape, one that tells
progress from repetition. An unbounded run left until it terminates by itself
would do it, and so would a trace of what the process is executing, read for
whether the work advances. Neither has been run here.

## The rest of the diagnosis

What the earlier runs on this processor recorded, with one exception below:

- `claude --version` answers, `2.1.272`;
- two default routes, the emulator's own winning on metric;
- `api.anthropic.com` resolving in the sixth family only;
- `claude auth status` at its 30 second bound, `STAT Rl`, empty wait channel,
  no descendants, no open sockets;
- refusing the message bus outright: no difference;
- the processor, `QEMU Virtual CPU version 2.5+`, flags stopping at `sse2`.

The exception is the sixth family, which answered here:

```
== reachability over the sixth family ==
http 404 in 0.060216s
```

The control run recorded `curl: (7) Failed to connect to api.anthropic.com port
443 after 0 ms`, `http 000 in 0.000519s`, `exit 7`. The two manifests carry the
same `vm` block, the same agent version and the same pinned skills, so nothing
in the configuration differs. Whatever changed is outside it: the path from this
host to that address, on 16 September against 18 September. The refusal was a
property of that day's network, not of this configuration, and
`../vm-processor-control/` and `../vm-reviewed-cycle/` have been corrected to
say so.

**None of this touches the processor finding.** That rests on
`../vm-reviewed-cycle/` and `../vm-processor-control/`, a run in both directions
with one variable changed, and the sixth family was never the mechanism. The
agent did not answer in the control run, when the sixth family refused, and it
did not answer in this run, when it worked.

## What this run does not show

That the guest is otherwise healthy: it stopped at the auth gate, so the task,
the review and the check are all `SKIPPED` in `manifest.json`, and the export
names the task artifact as the one thing it could not re-read, which is what a
run that never wrote it should say. Nor does it show why a processor without
those flags makes that program behave this way. That is a question about the
program, which nothing here opens.
