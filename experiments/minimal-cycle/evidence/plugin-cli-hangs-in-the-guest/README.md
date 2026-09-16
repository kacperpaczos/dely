# `claude plugin marketplace add` does not return in this guest

The documented way to install a plugin is
`claude plugin marketplace add <path>` followed by `claude plugin install`.
Both are used by nothing here, and this is why.

## What was measured

Inside the tool image build — an Ubuntu 24.04 guest under qemu, provisioned
over ssh — the command was given ten minutes with stdin closed:

```
timeout --signal=TERM --kill-after=30 600 claude plugin marketplace add /opt/dely-cycle/superpowers
status=124
```

`124` is the deadline. Its captured output was empty: not an error, not a
prompt, nothing at all. `build-transcript.txt` is the run, including the guest's
own `free -m` and `df -h` immediately before it.

## What that rules out

| Suspected | Observed |
|---|---|
| The guest ran out of memory | 3411 MB available, 0 swap used |
| The guest ran out of disk | 19 GB free on `/` |
| A prompt waiting for a terminal | the same command answers in under a second in a container **with** a terminal attached |
| stdin left open | it was closed here, and closing it changes nothing in a container either |
| A fresh home that never completed onboarding | `~/.claude.json` was written with `hasCompletedOnboarding` first; before that, the command hung the same way |
| A corrupt `~/.claude.json` | Claude Code reports that one loudly and exits 1 — this produced no output at all |
| `CI=1` in the environment | the same command with `CI=1` answers in a container |
| Orca being installed alongside | reproduced with the pinned Orca package installed in the container; still answers |
| Running as an ordinary user rather than root | answers as either |

Each row was a separate container run against the same checkout at the same
commit. None of them reproduced the hang.

## What it does not establish

Why. Something about this guest — its network is qemu's user-mode stack during
the build, and a graphical session is enabled — makes that command wait
forever, and what exactly was not determined. It is a lead, not a finding.

## What was done about it

Nothing here depends on that command any more. Superpowers is cloned at its
pinned commit and its skills are copied into `~/.claude/skills/`, which is the
same place Orca's own skills land and the place the runner then reads. Both
backends install the same way, so their evidence compares.

What that loses is the plugin's own wiring, its session hook among it. The
skills are present and are the pinned bytes — the runner compares every one of
them against the checkout, inside the environment, before it dispatches
anything — and the hook is not there. Nothing here should be read as saying a
plugin is installed.
