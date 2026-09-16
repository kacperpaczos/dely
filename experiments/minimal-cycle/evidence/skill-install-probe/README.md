# Installing the pinned skills: what the documented path actually does

`orca skills install --skill orchestration --skill orca-cli --agent claude`
is the documented way to put Orca's own skills where an agent will find them.
Asked with `--dry-run --json`, it says what it will run:

```
npx --yes skills add https://github.com/stablyai/orca \
  --skill orca-cli --skill orchestration --global --agent claude -y
```

That command was run in a throwaway container — not in a cycle environment,
because the question is about the command, not about this host. `transcript.txt`
is the whole session. Three things came out of it, and each changed the build.

**Ubuntu's own node cannot run it.** `ubuntu:24.04` packages node 18.19.1, and
the skills command line dies on it inside its own module loader before it does
anything. So the image and the container both carry the pinned node tarball,
whose digest is checked before it is unpacked, rather than the distribution
package.

**The agent name Orca passes is one that command rejects.** On a node that can
run it, the command answers `Invalid agents: claude` and lists what it does
accept, which includes `claude-code`. So the installs here call `skills add`
directly with `--agent claude-code` instead of going through
`orca skills install`.

**What it installs is what Orca's manifest says it should be.** The files land
in `~/.claude/skills/<name>/SKILL.md`, and their digests are exactly the
`exactSha256` values recorded for them in
`/opt/Orca/resources/skills/current-manifest.json` inside the pinned Orca
package. Those digests are what `host/versions.json` pins and what the runner
asks the environment for before it dispatches anything.

## Reproducing it

The transcript was produced by one `podman run --rm docker.io/library/ubuntu:24.04`
executing the steps above in order. It needs network and nothing else; it
creates no Orca, touches no host state, and takes no admission slot.

## What this does not show

That an agent in a session *used* any of these skills. It shows the files are
the pinned ones and are where an agent looks. Loading and obedience are
separate questions, and this answers neither.
