# The images are not in this repository

Both captures landed and both are real. They are not committed here.

This repository's closure gates include a literal grep forbidding a capital
letter followed by digits anywhere in any tracked file. A compressed image
contains that byte pattern by chance, so committing a PNG fails a gate that
belongs to the package rather than to this experiment. Weakening somebody
else's gate to carry an illustration would be the wrong trade.

`DIGESTS.txt` records the sha256, name and size of each. The run's own
`../screenshot.json` records the same digests independently, computed inside
the environment and again after the bytes reached the host, so a copy can be
checked against either.

The frames are described in `../README.md`, including the thing they fail to
show: Orca's first-run wizard is modal over the panels in both of them.
