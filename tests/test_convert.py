from __future__ import annotations

import os
from pathlib import Path

from photos_tool import convert
from photos_tool.convert import ConversionError


def test_live_photo_motion_detection_is_sibling_still_based(tmp_path: Path):
    live_mov = tmp_path / "IMG_0001.MOV"
    live_still = tmp_path / "IMG_0001.HEIC"
    standalone = tmp_path / "VID_0002.MOV"
    live_mov.write_text("video", encoding="utf-8")
    live_still.write_text("still", encoding="utf-8")
    standalone.write_text("video", encoding="utf-8")

    assert convert.is_live_photo_motion(live_mov)
    assert not convert.is_live_photo_motion(standalone)


def test_convert_videos_skips_live_motion_and_current_outputs(tmp_path: Path, monkeypatch):
    live_mov = tmp_path / "IMG_0001.MOV"
    live_still = tmp_path / "IMG_0001.HEIC"
    current_mov = tmp_path / "VID_0002.MOV"
    current_mp4 = tmp_path / "VID_0002.mp4"
    standalone = tmp_path / "VID_0003.MOV"
    non_hevc = tmp_path / "VID_0004.MOV"
    for path in (live_mov, live_still, current_mov, current_mp4, standalone, non_hevc):
        path.write_text(path.name, encoding="utf-8")
    os.utime(current_mp4, (current_mov.stat().st_atime + 10, current_mov.stat().st_mtime + 10))

    transcoded: list[Path] = []

    def fake_codec(path: Path) -> str:
        return "h264" if path == non_hevc else "hevc"

    def fake_transcode(source: Path, output: Path, crf: int = 20) -> None:
        transcoded.append(source)
        output.write_text(f"{source}:{crf}", encoding="utf-8")

    monkeypatch.setattr(convert, "probe_video_codec", fake_codec)
    monkeypatch.setattr(convert, "transcode_to_mp4", fake_transcode)

    summary = convert.convert_videos(tmp_path, crf=22)

    assert summary.scanned == 4
    assert summary.skipped_live == 1
    assert summary.skipped_existing == 1
    assert summary.skipped_non_hevc == 1
    assert summary.transcoded == 1
    assert transcoded == [standalone]


def test_convert_videos_excludes_compat_tree(tmp_path: Path, monkeypatch):
    archive_video = tmp_path / "2024" / "01" / "VID_0001.MOV"
    compat_video = tmp_path / "compat" / "2024" / "01" / "VID_0001.MOV"
    archive_video.parent.mkdir(parents=True)
    compat_video.parent.mkdir(parents=True)
    archive_video.write_text("archive", encoding="utf-8")
    compat_video.write_text("compat", encoding="utf-8")

    transcoded: list[Path] = []
    monkeypatch.setattr(convert, "probe_video_codec", lambda _path: "hevc")

    def fake_transcode(source: Path, output: Path, crf: int = 20) -> None:
        transcoded.append(source)
        output.write_text("mp4", encoding="utf-8")

    monkeypatch.setattr(convert, "transcode_to_mp4", fake_transcode)

    summary = convert.convert_videos(tmp_path)

    assert summary.scanned == 1
    assert transcoded == [archive_video]


def test_convert_videos_caches_non_hevc_probe(tmp_path: Path, monkeypatch):
    video = tmp_path / "VID_0001.MOV"
    cache = tmp_path / "cache.json"
    video.write_text("video", encoding="utf-8")
    probes = 0

    def fake_codec(_path: Path) -> str:
        nonlocal probes
        probes += 1
        return "h264"

    monkeypatch.setattr(convert, "probe_video_codec", fake_codec)

    first = convert.convert_videos(tmp_path, cache_path=cache)
    second = convert.convert_videos(tmp_path, cache_path=cache)

    assert first.skipped_non_hevc == 1
    assert second.skipped_non_hevc == 1
    assert probes == 1


def test_find_video_candidates_maps_walk_oserror(tmp_path: Path, monkeypatch):
    def broken_rglob(self: Path, _pattern: str):
        if self == tmp_path:
            raise OSError("network dropped")
        return ()

    monkeypatch.setattr(Path, "rglob", broken_rglob)

    try:
        convert.find_video_candidates(tmp_path)
    except ConversionError as exc:
        assert "network dropped" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ConversionError")
