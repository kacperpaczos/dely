# The control: put the processor back and it stops again

`../vm-reviewed-cycle/` settled after the guest was given this machine's own
processor. One change is not a cause, so this run puts the old one back and
changes nothing else.

| | `cpu_mode: host-passthrough` | `cpu_mode: default` |
|---|---|---|
| status | `SETTLED` in 153 s | `BLOCKED` |
| where | check passed, both agents settled | the agent never said whether it is signed in |

Both directions, one variable.

## What the guest's processor actually is

With nothing said about it, the emulator chooses:

```
Vendor ID:     GenuineIntel
Model name:    QEMU Virtual CPU version 2.5+
Flags:         fpu de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat
               pse36 clflush mmx fxsr sse sse2 syscall nx lm constant_tsc
               nopl xtopology cpuid tsc_known_freq pn
```

The flags stop at `sse2`. There is no `sse4_2`, no `popcnt`, no `aes`, no
`avx`. `claude --version` still answers on it; anything that does real work
does not.

## What the rest of the diagnosis said, again

Identical to the earlier runs, which is the point — nothing else moved:

- two default routes, the emulator's own winning on metric;
- `api.anthropic.com` in the sixth family only, refused there in half a
  millisecond on 16 September, reachable in the fourth in 49 ms with an
  http 404;
- `claude auth status` at its deadline, `STAT Rl`, empty wait channel, no
  descendants, no open sockets;
- refusing the message bus outright: no difference.

## The refusal in the sixth family was not invariant

This list was written as the picture that holds across these runs, and one line
of it does not belong there. **A later run disproved it.**
`../vm-processor-deadline/`, on 18 September, is the same configuration again,
and it found the sixth family answering: `http 404 in 0.060216s`, where
`auth-diagnosis.txt` here records `curl: (7) Failed to connect to
api.anthropic.com port 443 after 0 ms`, `http 000 in 0.000519s` and `exit 7`.
The name still resolved in the sixth family only, and everything else in the
list was the same in both.

The two manifests carry the same `vm` block, the same agent version and the same
pinned skills, so nothing in the configuration differs, and no setting reachable
from here touches an address family. What moved is the path from this host to
that address, between one day and the other. The measurement above is what was
seen on 16 September and stands as that. It was never a property of the finding.

**This does not weaken the processor result.** That rests on the run in both
directions with one variable changed, and the sixth family was never the
mechanism: the agent was measured running rather than waiting, holding nothing
open and having started nothing, and it still did not answer on 18 September,
when the sixth family worked.

## What is not established

Whether that command would ever finish on this processor. The probe that gives
it room was itself cut off by the run's command deadline — 180 seconds, shorter
than the 240 it had been given — so all this shows is "not within 180 seconds".
Stuck and unusably slow look the same here and this does not separate them. The
deadline was raised afterwards, and `../vm-processor-deadline/` is the run that
used it: the probe finished, the command was killed at 240 seconds having
printed nothing, and stuck and unusably slow are still not separated, because a
bounded probe cannot separate them.

And why a processor without those flags makes that program spin rather than run
slowly is a question about the program, which nothing here opens.
