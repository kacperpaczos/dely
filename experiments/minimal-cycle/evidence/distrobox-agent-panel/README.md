# The frame that finally holds an agent's panel

The first capture in which the centre of the window is a running agent rather
than the application's empty state.

    status:    SETTLED
    identity:  ENVIRONMENT
    run:       run_4d1892f8bfe4
    dispatches: ctx_96788fc96047 (implementer), ctx_b0d3e380e8d8 (reviewer)
    review:    accept, on the same 8-line diff the implementer produced
    check:     OK (exit 0)

## What was wrong, and it was not the wizard

Answering the application's own first-run questions removed the wizard, the
command-line modal, the consent banner and the star toast. The frame underneath
them was still not the agents' panels: it was the application's own empty state,
the wordmark over *"Select a workspace from the sidebar to begin."*, with the
project and its worktree sitting in the sidebar and the status bar counting
three live terminals. Nothing covered the panels. Nothing was drawing them.

Workspace selection is what mounts a terminal panel, and a run that registers a
repository, opens a coordinator terminal and dispatches two agents selects
nothing. So the run now asks, by name, for each agent's panel — `orca terminal
switch --terminal <handle>`, addressed by the handle that agent's own dispatch
returned — immediately before the last picture. The runtime's focus path reveals
the session through the window, and the window's handler for that reveal selects
the worktree the terminal belongs to before it activates the tab.

## What the receipt says

`screenshot.json` carries the two images and, beside them, the two rows that
explain the second one:

    implementer  navigated: true
    reviewer     navigated: true

`navigated` is the runtime's own answer to *did the window move*, and it is what
separates a drawn panel from a command that merely exited zero. It is false when
the terminal has no live process, when no window is attached to the runtime, and
when a later switch superseded this one — none of which changes the exit code.

## What the picture holds

The image itself is not committed; this is the receipt for it. In it:

- A tab strip across the top with all three terminals of the run — the
  coordinator's shell, the implementer's agent tab carrying a settled tick, and
  the reviewer's agent tab, foreground.
- The centre pane is the reviewer's session, legible from its task block down to
  its verdict: `accept`, the diff digest it read, and the three checks it made
  of the working tree.
- The sidebar row for the worktree is selected, with a chip per agent session.
- The right dock has become the project's file tree, with the file the task
  created marked as added.

The implementer's panel is **not** in the frame. The application foregrounds one
tab at a time, so the last switch decides which panel is drawn and the other is
its neighbour in the tab strip. One image cannot hold both, and this is the
reason rather than a failure of the switch.

## What this does not show

Two overlays remain and neither has a seeded field behind it: the update panel
in the lower right (*"Orca v1.4.205 is ready"*) and a usage toast in the lower
left, which covers the left of the reviewer's last three lines. Both are network
or state driven and were left alone deliberately.

It does not show the machine backend, a second task, or both agents' panels at
once — see above for why the last of those is not available from the command
line.

The identifiers are the run's own.
