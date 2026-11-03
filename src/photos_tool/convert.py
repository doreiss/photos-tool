"""Windows-playable video conversion, using macOS's built-in ``avconvert``.

``avconvert`` ships with every macOS and drives Apple's hardware encoder, so the .app needs no
ffmpeg. H.264 sources are repackaged into MP4 losslessly (``PresetPassthrough``: the video
bitstream is copied untouched); anything else — iPhone HEVC, HDR or not — is re-encoded to H.264
High (``PresetHighestQuality``) at the source resolution, which any Windows PC plays without an
add-on. ``avconvert`` strips capture date / GPS / camera tags (Apple's privacy filter), so they are
copied back from the source with exiftool. Every output is assembled in a same-dir temp and
published with ``os.replace`` only once complete, so a failure never leaves a half-made MP4 that a
later run (or the deletion gate) would mistake for a finished copy.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

VIDEO_SUFFIXES = {".mov", ".m4v"}
STILL_SUFFIXES = {".heic", ".heif", ".jpg", ".jpeg"}
# exiftool's QuickTime CompressorID (the sample-description fourcc).
H264_CODECS = {"avc1", "avc3"}
HEVC_CODECS = {"hvc1", "hev1", "dvh1", "dvhe"}  # incl. Dolby Vision HEVC sample entries
# Stamped on every MP4 we publish: the Photos asset UUID it was made from, and a signature of the
# exact source it was made from. A re-send recognises its own earlier output by the UUID (and never
# mistakes a *different* video's same-named file for it), and remakes it if the signature shows the
# video changed since (e.g. trimmed in Photos), so the deletion gate never trusts a stale copy.
IDENTITY_TAG = "XMP-xmpMM:OriginalDocumentID"
SIGNATURE_TAG = "XMP-xmpMM:InstanceID"


class ConversionError(RuntimeError):
    """Raised when a Windows-playable copy cannot be created."""


@dataclass(frozen=True)
class ConvertSummary:
    transcoded: int = 0
    skipped: int = 0


def find_video_candidates(root: Path, exclude_dirs: tuple[str, ...] = ("compat",)) -> list[Path]:
    candidates: list[Path] = []
    try:
        paths = root.rglob("*")
        for path in paths:
            if _is_excluded(path, root, exclude_dirs):
                continue
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
                candidates.append(path)
    except OSError as exc:
        raise ConversionError(f"could not walk {root}: {exc}") from exc
    return sorted(candidates)


def is_live_photo_motion(video: Path) -> bool:
    if video.suffix.lower() not in VIDEO_SUFFIXES:
        return False
    stem = video.stem.casefold()
    try:
        siblings = list(video.parent.iterdir())
    except OSError:
        return False
    return any(
        sibling.is_file()
        and sibling.stem.casefold() == stem
        and sibling.suffix.lower() in STILL_SUFFIXES
        for sibling in siblings
    )


def convert_videos(root: Path, compat_root: Path) -> ConvertSummary:
    """Mirror standalone HEVC videos in ``root`` as H.264 MP4s under ``compat_root``.

    The "original" format's optional compat mirror: the originals tree stays pristine and the
    Windows-friendly ``.mp4`` lands in ``compat_root`` at the same relative path. Live Photo motion
    clips and already-playable (non-HEVC) videos are skipped. ``_is_output_current`` keeps repeat
    runs idempotent without any on-disk cache.
    """
    summary = ConvertSummary()
    for video in find_video_candidates(root):
        if is_live_photo_motion(video):
            summary = _add(summary, skipped=1)
            continue
        output = (compat_root / video.relative_to(root)).with_suffix(".mp4")
        if _is_output_current(video, output):
            summary = _add(summary, skipped=1)
            continue
        codec = probe_video_codec(video)
        if codec not in HEVC_CODECS:
            summary = _add(summary, skipped=1)
            continue
        make_viewable_mp4(video, output, codec=codec)
        summary = _add(summary, transcoded=1)
    return summary


def probe_video_codec(path: Path) -> str:
    """The video's QuickTime CompressorID (e.g. ``hvc1``, ``avc1``), lower-cased; ``""`` if none."""
    result = _run(["exiftool", "-s3", "-CompressorID", str(path)])
    lines = result.stdout.strip().splitlines()
    return lines[0].strip().lower() if lines else ""


def read_stamp(path: Path) -> tuple[str | None, str | None]:
    """(Photos UUID, source signature) stamped on an MP4 we published; Nones if absent/foreign."""
    try:
        out = _run(["exiftool", "-j", f"-{IDENTITY_TAG}", f"-{SIGNATURE_TAG}", str(path)]).stdout
        record = json.loads(out)[0]
    except (ConversionError, ValueError, IndexError, TypeError):
        return None, None
    identity = record.get(IDENTITY_TAG.split(":")[-1])
    signature = record.get(SIGNATURE_TAG.split(":")[-1])
    return (str(identity) if identity else None, str(signature) if signature else None)


def make_viewable_mp4(
    source: Path,
    output: Path,
    *,
    identity: str | None = None,
    signature: str | None = None,
    codec: str | None = None,
) -> None:
    """Write a Windows-playable H.264 MP4 of ``source`` to ``output``, atomically.

    ``identity`` (a Photos UUID) and ``signature`` (of ``source``), when given, are stamped on the
    file so a later run can recognise it and tell whether it is still current. ``codec`` skips a
    probe the caller already did. An existing ``output`` is replaced only once the new one is whole.
    """
    # Same dir => same filesystem => os.replace is atomic. Keep a .mp4 extension: avconvert picks
    # the container from it.
    tmp = output.with_name(output.stem + ".partial.mp4")
    presets = ["PresetHighestQuality"]
    if (codec if codec is not None else probe_video_codec(source)) in H264_CODECS:
        # Lossless repackage first; fall back to a re-encode if the streams can't go in MP4 as-is.
        presets.insert(0, "PresetPassthrough")
    tag_cmd = [
        "exiftool",
        "-tagsFromFile",
        str(source),
        "-api",
        "QuickTimeUTC=1",
        "-all:all",
    ]
    if identity:
        tag_cmd.append(f"-{IDENTITY_TAG}={identity}")
    if signature:
        tag_cmd.append(f"-{SIGNATURE_TAG}={signature}")
    tag_cmd += ["-overwrite_original", str(tmp)]
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        _avconvert(source, tmp, presets)
        # The point of this file is that Windows can play it: refuse to publish anything that
        # isn't H.264 (e.g. if a future macOS changed what a preset emits).
        produced = probe_video_codec(tmp)
        if produced not in H264_CODECS:
            raise ConversionError(f"avconvert produced {produced or 'no'} video, not H.264")
        _run(tag_cmd)
        stat = source.stat()
        os.utime(tmp, (stat.st_atime, stat.st_mtime))
        os.replace(tmp, output)  # atomic publish: the output appears only fully-formed
    except ConversionError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise ConversionError(f"could not finalize {output}: {exc}") from exc


def _avconvert(source: Path, output: Path, presets: list[str]) -> None:
    error: ConversionError | None = None
    for preset in presets:
        try:
            _run(
                [
                    "avconvert",
                    "--source",
                    str(source),
                    "--output",
                    str(output),
                    "--preset",
                    preset,
                    "--replace",
                ]
            )
            return
        except ConversionError as exc:
            error = exc
    assert error is not None  # presets is never empty
    raise error


def _is_output_current(source: Path, output: Path) -> bool:
    try:
        return output.exists() and output.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def _is_excluded(path: Path, root: Path, exclude_dirs: tuple[str, ...]) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return any(part in exclude_dirs for part in relative.parts[:-1])


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ConversionError(f"{cmd[0]} was not found on PATH") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ConversionError(f"{' '.join(cmd)} failed with exit {result.returncode}: {detail}")
    return result


def _add(summary: ConvertSummary, **changes: int) -> ConvertSummary:
    return ConvertSummary(
        transcoded=summary.transcoded + changes.get("transcoded", 0),
        skipped=summary.skipped + changes.get("skipped", 0),
    )
