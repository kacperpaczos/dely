# One complete machine cycle, and what was stopping it

`SETTLED`, 153 seconds, on a per-run libvirt domain declared by Pulumi over an
overlay on the preserved tool image. The container backend's cycle takes 156
seconds, so the two are not far apart.

## What had been stopping it

Every machine run before this one blocked in the same place, and for most of
this project's history the symptom was described from much further away: the
worker's turn was never observed. Closer up it was one command not returning.

The path to the cause, with what each step ruled out:

| asked | answered |
|---|---|
| does the binary run at all | `claude --version` answers |
| is there a route | two default routes; the emulator's own wins on metric |
| does the name resolve | `api.anthropic.com`, sixth family only |
| is it reachable | http 404 in 53 ms |
| over the fourth family | http 404 in 60 ms |
| over the sixth | refused in 0.5 ms — refused, not swallowed |
| with the fourth preferred | still no answer |
| is it a message bus | refusing one outright changes nothing, and it spawned no child |
| **what is it doing** | `STAT Rl`, empty wait channel, **no descendants, no open sockets** |

A process that is running rather than waiting, holding nothing open and having
started nothing, is not blocked on anything. It is doing work. And the domain
said nothing at all about its processor, so the emulator had been choosing a
conservative model of its own.

Giving the guest this machine's processor — `cpu_mode: host-passthrough`,
applied through the same transform that already adds the emulator-handled
interface — is the only thing that changed between the last blocked run and
this one. `../vm-processor-control/` is the run that puts that back and shows
it blocking again.

## The two agents

```
implementer  dispatch ctx_ee657f4ebe52  terminal term_48127c4e…  delivery delivery_917c8bcfe98a
reviewer     dispatch ctx_c432b7caa4de  terminal term_de49329d…  delivery delivery_c9c3bc57a6ba
both under   run_ba8c0d8d3b38
```

Two dispatches, two agent terminals, two deliveries, one Run. The reviewer was
handed the diff by path and returned `accept` naming the same digest the
capture did: *"The diff only creates evidence.txt with exactly
'dely-cycle-marker' and no trailing newline, matching the requested change."*

## Everything else it established

- **Identity** — `ENVIRONMENT`: its own machine identity, home and project copy.
- **Auth** — the agent inside the guest reported itself signed in through the
  copied entry, which was removed afterwards and the removal verified.
- **Skills** — all three pinned skills present in the guest at the pinned
  revision, from the image rather than from a run-time fetch.
- **Screen** — four windows appeared on the guest's own screen that were not
  there before the application started.
- **Check** — the independent check read the file and exited 0.
- **The operator's own Orca** — none of the 38 files in their registry names
  this run.
- **Cleanup** — `DESTROYED`: domain, overlay, seed, stack and per-run state
  gone; the tool image untouched; no process left on the host.

## What it does not show

A second, different task, or a run on any host but this one. Why a conservative
processor model makes that command spin rather than merely run slowly — the
control run measures the symptom, not the mechanism inside the agent.
