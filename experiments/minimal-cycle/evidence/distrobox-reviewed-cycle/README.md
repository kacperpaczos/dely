# One complete container cycle, with a review that was really a review

`SETTLED`, 156 seconds, on this host. Every gate the runner has was passed and
each one left something a reader can check.

## The two agents

```
implementer  dispatch ctx_ad5288d13ac1  terminal term_eb28f24b…  delivery delivery_805b00e5fd73
reviewer     dispatch ctx_095c48d4a3b6  terminal term_832effc8…  delivery delivery_3644e0fd8549
both under   run_bc0193962f3a
```

Two dispatches, two agent terminals, **two deliveries**, one Run. The two
deliveries are the part that matters: an earlier run had one identifier across
both, which is what a bound Run replaying an unacknowledged batch looks like
(`../replayed-delivery/`). Layout, focus and panel position were not consulted
for any of this; the identifiers were.

## The review was of this diff

| | |
|---|---|
| captured from the environment | `1702d4bb…dd88a`, 8 lines |
| still on disk when the review ended | `1702d4bb…dd88a` |
| what the reviewer said it read | `1702d4bb…dd88a` |
| verdict | `accept` |

The reviewer's own reason: *"The diff only adds evidence.txt, and the file
holds exactly dely-cycle-marker with no trailing newline."* It had the diff,
and what it says is about what is in it — `handoff-diff.patch` is the file it
was handed.

## Everything else the run established

- **Identity** — `ENVIRONMENT`: its own machine identity, home and project copy,
  not the host's.
- **Auth** — the copied entry was not merely copied: the agent inside the box
  reported itself signed in through `claude.ai`. Afterwards it was removed and
  the removal verified.
- **Skills** — all three pinned skills present in the box at the pinned
  revision: Orca's `orchestration` and `orca-cli` by digest, Superpowers' 14
  compared against the checkout at its pinned commit.
- **Screen** — one window appeared on the screen this run created that was not
  there before it started the application, of two there afterwards.
- **Check** — the independent check, run outside both agent sessions, observed
  the marker and exited 0.
- **Admission** — the run held the one slot this host allows for the backend,
  and released it.
- **The operator's own Orca** — none of the 38 files in their registry names
  this run.
- **Cleanup** — `DESTROYED`: every per-run resource gone, every shared one kept,
  and no process on the host still holding a path of this run.

## What it does not show

A second, different task, or a run on any host but this one. Isolation: this
backend mounts the operator's home and shares their process table, and the run
records that rather than claiming otherwise. And that the reviewer read every
line of the diff — it shows it was handed that diff and answered about it.
