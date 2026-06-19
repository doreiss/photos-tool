"""Compatibility-copy conversion helpers."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VIDEO_SUFFIXES = {".mov", ".m4v"}
STILL_SUFFIXES = {".heic", ".heif", ".jpg", ".jpeg"}
HEVC_CODECS = {"hevc", "h265", "h.265"}


class ConversionError(RuntimeError):
    """Raised when a compatibility copy cannot be created."""


@dataclass(frozen=True)
class ConvertSummary:
    scanned: int = 0
    transcoded: int = 0
    skipped_live: int = 0
    skipped_existing: int = 0
    skipped_non_hevc: int = 0


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
    cache_path: Path | None = None,
) -> ConvertSummary:
    """Transcode standalone HEVC videos in ``root`` to H.264 MP4s under ``compat_root``.

    The originals tree stays pristine; the Windows-friendly ``.mp4`` mirror lands in
    ``compat_root`` at the same relative path. Live Photo motion clips are skipped.
    """
    summary = ConvertSummary()
    cache = _load_cache(cache_path)
    cache_changed = False
    for video in find_video_candidates(root):
        summary = _add(summary, scanned=1)
        if is_live_photo_motion(video):
            summary = _add(summary, skipped_live=1)
            continue
        output = (compat_root / video.relative_to(root)).with_suffix(".mp4")
        if _is_output_current(video, output):
            summary = _add(summary, skipped_existing=1)
            continue
        try:
            signature = _signature(video)
        except OSError as exc:
            raise ConversionError(f"could not stat {video}: {exc}") from exc
        cached = cache.get(str(video))
        if cached == signature:
            summary = _add(summary, skipped_non_hevc=1)
            continue
        codec = probe_video_codec(video)
        if codec not in HEVC_CODECS:
            cache[str(video)] = signature
            cache_changed = True
            summary = _add(summary, skipped_non_hevc=1)
            continue
        transcode_to_mp4(video, output, crf=crf)
        summary = _add(summary, transcoded=1)
    if cache_path is not None and cache_changed:
        _save_cache(cache_path, cache)
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


def _signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _load_cache(cache_path: Path | None) -> dict[str, dict[str, int]]:
    if cache_path is None:
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cache: dict[str, dict[str, int]] = {}
    for path, value in raw.items():
        if isinstance(path, str) and _is_signature(value):
            cache[path] = value
    return cache


def _save_cache(cache_path: Path, cache: dict[str, dict[str, int]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")


def _is_signature(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("size"), int)
        and isinstance(value.get("mtime_ns"), int)
    )


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
        scanned=summary.scanned + changes.get("scanned", 0),
        transcoded=summary.transcoded + changes.get("transcoded", 0),
        skipped_live=summary.skipped_live + changes.get("skipped_live", 0),
        skipped_existing=summary.skipped_existing + changes.get("skipped_existing", 0),
        skipped_non_hevc=summary.skipped_non_hevc + changes.get("skipped_non_hevc", 0),
    )
