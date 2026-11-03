"""The "viewable" format's video pass: every video lands on the share as a Windows-playable MP4.

osxphotos exports the send's videos (the edited version when there is one) into a local scratch
dir. That is a same-volume ``NSFileManager`` copy — an APFS clone — so even a large batch costs
almost no disk space on a Mac that is being freed up. Each one is then converted straight onto the
share by :func:`convert.make_viewable_mp4`, and the pass writes an osxphotos-shaped report whose
rows point at those MP4s. ``send`` reconciles it together with the photo pass, so the deletion gate
verifies the very file that backs each original — a video whose conversion failed is reported as an
error, never backed up, and never offered for deletion.

Re-sending is cheap and safe: each MP4 is stamped with its Photos UUID and a signature of the
source it was made from (:data:`convert.IDENTITY_TAG` / :data:`convert.SIGNATURE_TAG`). A video
already on the share and unchanged is recognised and not converted again; one edited since (e.g.
trimmed in Photos) is remade in place; and a *different* video that happens to share its name gets
``name (1).mp4`` instead of being mistaken for it.

Known cost: every send re-exports the selected videos into scratch before it can recognise the ones
already done. With the Photos library on the internal disk that is an APFS clone (no space, little
time); a library on an external drive makes real copies.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .convert import ConversionError, make_viewable_mp4, read_stamp
from .osxphotos_runner import ExportResult, OsxphotosError, run_export
from .plan import ExportOptions
from .report import load_report_rows
from .state import content_signature

_MAX_NAME_TRIES = 1000
# osxphotos's in-run collision suffix: "IMG_0001 (1).mov". It depends on what else is in that run,
# so finding a video's earlier MP4 looks across the whole same-named family, not one exact name.
_COLLISION_SUFFIX = re.compile(r"^(?P<base>.+) \(\d+\)$")


@dataclass(frozen=True)
class VideoPassResult:
    report_path: Path
    converted: int = 0
    reused: int = 0
    failed: int = 0
    export: ExportResult | None = None  # the osxphotos run, for --verbose


def send_videos(
    opts: ExportOptions,
    destination: Path,
    report_path: Path,
    *,
    dry_run: bool = False,
) -> VideoPassResult:
    """Export ``opts``' videos to scratch, publish an MP4 of each under ``destination``, and write
    the resulting rows to ``report_path``. ``dry_run`` asks osxphotos what it would export and
    converts nothing."""
    counts: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(prefix="photos-tool-videos-") as tmp:
        staging = Path(tmp) / "videos"
        raw_report = Path(tmp) / "report.json"
        scratch = replace(opts, destination=str(staging), exportdb=str(Path(tmp) / "export.db"))
        result = run_export(scratch, raw_report, dry_run=dry_run)
        if not result.ok:
            detail = (result.stderr or result.stdout or "").strip()[-500:]
            raise OsxphotosError(f"video export failed with exit {result.returncode}: {detail}")
        # osxphotos may write no report at all when the send holds no videos.
        rows = load_report_rows(raw_report) if raw_report.exists() else []
        if not dry_run:
            rows = [_publish(row, staging, destination, counts) for row in rows]
    report_path.write_text(json.dumps(rows), encoding="utf-8")
    return VideoPassResult(
        report_path,
        converted=counts["converted"],
        reused=counts["reused"],
        failed=counts["failed"],
        export=result,
    )


def _publish(
    row: dict[str, Any], staging: Path, destination: Path, counts: Counter[str]
) -> dict[str, Any]:
    row = dict(row)
    filename = row.get("filename")
    landed = row.get("exported") or row.get("skipped")
    if not filename or row.get("missing") or row.get("error") or not landed:
        return row  # nothing to convert: reconcile + the gate see exactly what osxphotos reported
    uuid = str(row.get("uuid") or "")
    source = Path(str(filename))
    try:
        # Compare resolved paths: macOS's temp dir sits behind a symlink (/var -> /private/var)
        # and osxphotos reports the resolved form.
        relative = source.resolve().relative_to(staging.resolve()).with_suffix(".mp4")
        signature = content_signature(source)
        target, already = _target_for(destination / relative, uuid, signature)
        if not already:
            make_viewable_mp4(source, target, identity=uuid or None, signature=signature)
    except (ConversionError, ValueError, OSError) as exc:  # one video fails, not the whole send
        print(f"Video not backed up ({source.name}): {exc}", file=sys.stderr)
        counts["failed"] += 1
        row.update(filename="", exported=False, new=False, skipped=False, error=True)
        return row
    counts["reused" if already else "converted"] += 1
    row.update(filename=str(target), exported=not already, new=not already, skipped=already)
    return row


def _target_for(first: Path, uuid: str, signature: str) -> tuple[Path, bool]:
    """Where this video's MP4 goes, and whether an up-to-date one is already there.

    ``first`` is the name osxphotos gave the video in scratch. If an MP4 stamped with THIS video's
    UUID already exists among its same-named family (``base.mp4``, ``base (n).mp4``) it is the one:
    reused if the signature still matches, else remade in place (the video changed since). Files
    stamped with anything else — another video, or nothing we can read — are never touched.
    Otherwise it gets osxphotos's own name if free (so a real "Clip (2)" keeps its name), else the
    first free ``base (n).mp4``.
    """
    match = _COLLISION_SUFFIX.match(first.stem)
    base_stem = match["base"] if match else first.stem
    if uuid:
        for candidate in _same_name_family(first.parent, base_stem, first.suffix):
            identity, stamped = read_stamp(candidate)
            if identity == uuid:
                return candidate, stamped == signature
    if not first.exists():
        return first, False
    for n in range(_MAX_NAME_TRIES):
        name = base_stem if n == 0 else f"{base_stem} ({n})"
        candidate = first.with_name(name + first.suffix)
        if not candidate.exists():
            return candidate, False
    raise ConversionError(f"no free name for {first.name} after {_MAX_NAME_TRIES} tries")


def _same_name_family(folder: Path, stem: str, suffix: str) -> list[Path]:
    """``stem.mp4`` and ``stem (n).mp4`` in ``folder``, case-insensitively (as an SMB share is)."""
    pattern = re.compile(rf"^{re.escape(stem)}( \(\d+\))?{re.escape(suffix)}$", re.IGNORECASE)
    try:
        return sorted(path for path in folder.iterdir() if pattern.match(path.name))
    except FileNotFoundError:
        return []  # folder not created yet: nothing sent there before
