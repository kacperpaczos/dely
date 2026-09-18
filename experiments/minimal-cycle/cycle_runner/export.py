"""Writing artifacts to the host and proving the host holds them.

Everything the run wants to keep goes through one exporter. Confirmation
re-reads each artifact from its host path and recomputes its digest, so a
receipt cannot describe bytes that never landed or that changed after the
write. Only a confirmed export releases the run to destroy anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import hashing, redact
from .proc import utc_now
from .result import ExportRecord
from .status import ExportStatus

RECEIPT_NAME = "export-receipt.json"

#: How much of one captured stream an artifact keeps. A single `apt-get`
#: install prints thousands of lines, and every provisioning step of a run
#: would otherwise land beside a manifest somebody has to read, so a stream is
#: bounded rather than complete. The *end* is what is kept: a command that
#: failed says why in its last lines, and a run that succeeded needs no more
#: than that either.
STREAM_LIMIT_BYTES = 64 * 1024

#: The first line of a stream that did not fit. A truncated log that reads as a
#: complete one is worse than no log at all: it invites a conclusion drawn from
#: whichever part happened to survive. So the artifact states that it is
#: bounded, and by how much, in its own text.
TRUNCATION_NOTICE = (
    "<bounded: {dropped} byte(s) dropped from the head of this stream; "
    "the last {kept} byte(s) follow>\n"
)


def bounded_stream(text: str, *, limit: int = STREAM_LIMIT_BYTES) -> bytes:
    """Return one captured stream as bytes, keeping at most `limit` of its end.

    A stream that fits is returned whole and unannotated. One that does not is
    the notice above followed by its last `limit` bytes, so nothing here can
    produce a file that looks complete and is not. The cut is taken on bytes,
    which is the unit the receipt and the notice both speak in; it may land
    inside a character, and that is a mangled first character rather than a
    silent loss.
    """
    raw = text.encode("utf-8", errors="surrogateescape")
    if len(raw) <= limit:
        return raw
    kept = raw[-limit:] if limit > 0 else b""
    notice = TRUNCATION_NOTICE.format(dropped=len(raw) - len(kept), kept=len(kept))
    return notice.encode("utf-8") + kept


class ExportError(ValueError):
    """An artifact path is not inside the run's own directory."""


@dataclass
class _Declared:
    required: bool
    expected_sha256: str | None = None


class Exporter:
    """Collects the run's artifacts under one host directory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._declared: dict[str, _Declared] = {}

    # -- placing artifacts ------------------------------------------------

    def _resolve(self, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ExportError(
                f"an artifact path must stay inside the run directory, got {relative!r}"
            )
        return self.root / candidate

    def declare(self, relative: str, *, required: bool = True) -> None:
        """Record that an artifact is expected without writing it here."""
        self._resolve(relative)
        self._declared[Path(relative).as_posix()] = _Declared(required=required)

    def write_bytes(self, relative: str, data: bytes, *, required: bool = True) -> Path:
        """Write a binary artifact and remember the digest it was written with."""
        target = self._resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self._declared[Path(relative).as_posix()] = _Declared(
            required=required, expected_sha256=hashing.digest_file(target)
        )
        return target

    def write_text(self, relative: str, text: str, *, required: bool = True) -> Path:
        """Write a redacted text artifact."""
        return self.write_bytes(
            relative, redact.text(text).encode("utf-8"), required=required
        )

    def write_stream(
        self,
        relative: str,
        text: str,
        *,
        extra_values: Sequence[str] = (),
        required: bool = True,
        limit: int = STREAM_LIMIT_BYTES,
    ) -> Path:
        """Write one command's captured stream: redacted first, then bounded.

        The order is the whole of it. Redaction matches shapes, and half a
        shape matches nothing, so bounding first would keep the tail of a token
        as ordinary text and write it out. The bound is therefore applied to
        text redaction has already been through — with this run's own forwarded
        values among the literals, because a value the runner was handed has no
        shape and only the literal removes it — and never the other way round.
        """
        return self.write_bytes(
            relative,
            bounded_stream(redact.text(text, extra_values), limit=limit),
            required=required,
        )

    def write_json(
        self, relative: str, document: Mapping[str, Any] | list, *, required: bool = True
    ) -> Path:
        """Write a redacted document as indented JSON."""
        payload = json.dumps(redact.structure(document), indent=2, sort_keys=True) + "\n"
        return self.write_bytes(relative, payload.encode("utf-8"), required=required)

    def adopt_tree(self, relative_dir: str, source: Path, *, required: bool = False) -> None:
        """Copy a directory into the run and declare every file it carried."""
        destination = self._resolve(relative_dir)
        source = Path(source)
        prefix = Path(relative_dir).as_posix()
        if not source.is_dir():
            self._declared[f"{prefix}/"] = _Declared(required=required)
            return
        copied = 0
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            self._declared[(Path(prefix) / relative).as_posix()] = _Declared(
                required=False, expected_sha256=hashing.digest_file(target)
            )
            copied += 1
        if copied == 0:
            self._declared[f"{prefix}/"] = _Declared(required=required)

    # -- proving the host holds them --------------------------------------

    def confirm(self) -> ExportRecord:
        """Re-read every declared artifact and write the receipt."""
        artifacts: list[dict[str, Any]] = []
        problems: list[str] = []
        blocking = 0
        for relative in sorted(self._declared):
            declared = self._declared[relative]
            target = self.root / relative
            if not target.is_file():
                problems.append(f"{relative} (absent from the host)")
                blocking += int(declared.required)
                continue
            described = hashing.describe_file(target, relative_to=self.root)
            if (
                declared.expected_sha256 is not None
                and described["sha256"] != declared.expected_sha256
            ):
                problems.append(f"{relative} (digest changed after the write)")
                blocking += int(declared.required)
                continue
            artifacts.append(described)

        if blocking == 0:
            state = ExportStatus.CONFIRMED
        elif artifacts:
            state = ExportStatus.PARTIAL
        else:
            state = ExportStatus.FAILED

        record = ExportRecord(
            status=state,
            confirmed_at=utc_now() if state is ExportStatus.CONFIRMED else None,
            receipt_path=RECEIPT_NAME,
            artifacts=artifacts,
            missing=problems,
            detail=(
                f"{len(artifacts)} artifact(s) re-read from the host; "
                f"{len(problems)} unresolved"
            ),
        )
        receipt = self.root / RECEIPT_NAME
        receipt.write_text(
            json.dumps(redact.structure(record.to_document()), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return record
