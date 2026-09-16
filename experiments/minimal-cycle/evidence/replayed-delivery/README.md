# Two dispatches, one delivery: the review that never ran

This run reached further than any before it and stopped at the handoff. It is
kept because of *how* it stopped: the identifiers show a defect that a green
run would have hidden completely.

## What the run did

| | |
|---|---|
| identity | `ENVIRONMENT` — its own machine identity, home and project copy |
| auth | the agent in the box reported itself signed in through `claude.ai` |
| skills | all three pinned skills present in the box and the pinned bytes |
| screen | one window appeared on the screen the run created, of two there |
| task | the implementer wrote the marker; the captured diff is `handoff-diff.patch` |
| review | two dispatches, two agent terminals, both under one Run |
| cleanup | `DESTROYED`; nothing of it left on the host |

## What it shows

Look at the two dispatches in `review.json` and `manifest.json`:

```
implementer  dispatch ctx_aacbb0d0378e  terminal term_fe0ba831…  delivery delivery_ad649bb37960
reviewer     dispatch ctx_0ed3797bc0e6  terminal term_b37cd799…  delivery delivery_ad649bb37960
```

Two dispatches, two terminals — and **one delivery identifier between them**.

`orca orchestration check` says why: *a bound Run replays the same Delivery
until `--ack`*. The reviewer's wait was on the implementer's Run, so it woke
immediately on the implementer's `worker_done` and reported that agent's
outcome, `succeeded`, as its own. The record says `reviewer: status OK, outcome
succeeded` and it is not true: that dispatch had barely started.

Nothing about the reviewer's *result* would have looked wrong. It settled, it
settled quickly, and it reported success.

## What caught it

Not the outcome — the artifact. The reviewer was asked to write a verdict
naming the digest of the diff it read, and there was no verdict file, so the
run failed on `the reviewer did not report which diff it read, so its verdict
cannot be tied to this one`. A review is tied to what it reviewed or it is not
a review.

## What was changed

The second dispatch now acknowledges the batch the first one settled on, and
each wait's delivery identifier is recorded — which is what makes a repeat of
this visible at a glance rather than by inference.
