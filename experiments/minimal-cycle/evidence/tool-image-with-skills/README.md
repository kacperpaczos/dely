# The tool image, and what it carries

`host/packer/build-tool-image` built this image from the verified Ubuntu 24.04
cloud image. `tool-image.json` is the metadata the build wrote beside it, and
is what preflight holds the image to: a packer build is not reproducible byte
for byte, so no digest for it is committed here.

`build-highlights.txt` is the provisioning, filtered to the lines that say what
went in. In order:

- **node v24.21.0**, from the pinned tarball with its digest checked, because
  Ubuntu's own node 18 cannot run the skills command line at all
  (`../skill-install-probe/`);
- **Orca 1.4.201** from the pinned package and digest, **Claude Code 2.1.272**
  from the registry at that version;
- **Superpowers at `b36e0829…`**, cloned and checked out at that commit, with
  its 14 skills copied into `~/.claude/skills/`. The plugin command line is not
  used: it does not return in this guest (`../plugin-cli-hangs-in-the-guest/`);
- **Orca's own `orchestration` and `orca-cli`**, whose installed `SKILL.md`
  files the build then compares against the digests
  `/opt/Orca/resources/skills/current-manifest.json` records inside the pinned
  package — `skill orchestration matches its pin`, `skill orca-cli matches its
  pin`. A mismatch fails the build.

The guest's own record of itself, written to `/etc/dely-cycle-image`, lists all
sixteen skills.

## What it does not carry

No credential. The build uses a throwaway key pair, removes it before shutdown,
and refuses to finish if a credentials file exists in the guest's home. The
metadata records `carries_credentials: false` and the build is what makes that
true rather than the field.

## What this does not establish

That an agent in a session loaded or obeyed any of these. It establishes which
files are in the image and that they are the pinned bytes. Before a run
dispatches anything it asks the environment the same question again, because an
image is not an environment.
