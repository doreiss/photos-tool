"""Compatibility-copy conversion helpers."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

VIDEO_SUFFIXES = {".mov", ".m4v"}
STILL_SUFFIXES = {".heic", ".heif", ".jpg", ".jpeg"}
HEVC_CODECS = {"hevc", "h265", "h.265"}


class ConversionError(RuntimeError):
    """Raised when a compatibility copy cannot be created."""


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


def convert_videos(
    root: Path,
    compat_root: Path,
    crf: int = 20,
) -> ConvertSummary:
    """Transcode standalone HEVC videos in ``root`` to H.264 MP4s under ``compat_root``.

    The originals tree stays pristine; the Windows-friendly ``.mp4`` mirror lands in
    ``compat_root`` at the same relative path. Live Photo motion clips are skipped.
    ``_is_output_current`` keeps repeat runs idempotent without any on-disk cache.
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
        transcode_to_mp4(video, output, crf=crf)
        summary = _add(summary, transcoded=1)
    return summary


def probe_video_codec(path: Path) -> str:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "default=nokey=1:noprint_wrappers=1",
        str(path),
    ]
    result = _run(cmd)
    codec = result.stdout.strip().splitlines()
    return codec[0].strip().lower() if codec else ""


def transcode_to_mp4(source: Path, output: Path, crf: int = 20) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-c:a",
        "aac",
        "-movflags",
        "use_metadata_tags",
        "-map_metadata",
        "0",
        str(output),
    ]
    _run(ffmpeg_cmd)
    exiftool_cmd = [
        "exiftool",
        "-tagsFromFile",
        str(source),
        "-api",
        "QuickTimeUTC=1",
        "-all:all",
        "-overwrite_original",
        str(output),
    ]
    _run(exiftool_cmd)
    try:
        stat = source.stat()
        os.utime(output, (stat.st_atime, stat.st_mtime))
    except OSError as exc:
        raise ConversionError(f"could not set mtime on {output}: {exc}") from exc


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
