# Control settles its terminal debt, and a question from 14 September is answered

`SETTLED` in 193 seconds. The first cycle in which Control disposed of the
terminals it owed, and the first with the skills installed from a pinned
commit rather than from whatever the repository's tip happened to be.

## The question, and the answer

> When a supervised worker finishes, does its terminal panel close — and if a
> panel disappears, can that be told from the terminal still existing?

**Orca does not close it. Control must. And yes, it is distinguishable — from
the live list, without looking at a screen.**

`orca terminal list`, either side of the two releases:

```
before   term_4fcd9aae…   term_e286436b…   term_4d57c24e…
after    term_4fcd9aae…
```

Three live before, one after. The two that went are exactly the two agent
terminals; the one that stayed is the coordinator's own — which is `worker-release`
refusing to close anything but the worker's own agent terminal, observed rather
than read from a help page.

The record's own words: *"a terminal whose panel is merely off screen stays in
this list, so this is a closure rather than a disappearance."*

## What the plane answered

Both dispatches were owed before the releases and neither after:

```
ctx_8f80cf372eb8  implementer  ->  released
ctx_729fe53c01fc  reviewer     ->  released
owed after: (none)
```

`released` is the word, and it arrives in the reply's `state`. That matters:
the command's help enumerates the four outcomes but does not name the field,
and the first implementation guessed `outcome` first. The guess was harmless
only because `state` was tried third.

## The two agents

```
implementer  dispatch ctx_8f80cf372eb8  terminal term_e286436b…  delivery delivery_ab010f1acc42
reviewer     dispatch ctx_729fe53c01fc  terminal term_4d57c24e…  delivery delivery_5e0baa3b3f90
both under   run_b3537244a4b7
```

Two dispatches, two terminals, two deliveries, one Run. The reviewer returned
`accept`: *"The diff adds only evidence.txt containing exactly
'dely-cycle-marker' with no trailing newline, confirmed by the blob hash
2945440, and touches nothing else."* It cited the blob hash, which is in the
patch it was handed.

## The pictures, and what they do not show

Both moments landed, taken by the box's own X server on `:99` — not by the
application, and not from the operator's screen. The images themselves are not
committed: this repository's gates forbid a byte pattern every compressed image
eventually contains, so `screenshots/` carries their digests and sizes instead,
and `screenshot.json` records the same digests independently.

**Neither frame shows the agents' panels.** Orca's first-run wizard is modal
over the whole window — *"WELCOME TO ORCA / Pick your default agent, 1 of 4"* —
and it is still there at the end of the review. Behind it the IDE shell, the
project tree and the edges of panes are visible.

So the capture works and the evidence it was built to produce is not in it.
What the frames do prove is narrower and still worth having: the image is of
the screen this run created, at 1280x800, with the box's own Orca on it. The
first-run state the runner writes answers the agent's four questions, which is
why dispatch works from behind the wizard — but it does not dismiss the wizard,
and that is a different problem from the one that was solved.

## Everything else

- **Skills** — all three present at the pinned revision, now fetched from commit
  `fb322046…` rather than from the repository's tip. The run before this one
  blocked here, correctly, when upstream moved ahead of what Orca 1.4.201 ships.
- **Auth** — the agent in the box reported itself signed in through the copied
  entry; removed afterwards and the removal verified.
- **Display** — one window appeared on the screen this run created.
- **Check** — the independent check observed the marker and exited 0.
- **Host registry** — none of the operator's 38 registry files names this run.
- **Cleanup** — `DESTROYED`, nothing left holding a path of it.

## What this does not establish

Whether a *retained* terminal behaves as documented — only release was
exercised. What happens to the terminal of a worker that never settles: it stays
`active`, owes nobody a decision by the contract, and no run here has tested it.
And whether a single frame can ever hold both panels, which needs the first-run
wizard gone, not a better camera.
