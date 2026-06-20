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


def test_convert_videos_writes_mp4_into_compat_tree(tmp_path: Path, monkeypatch):
    root = tmp_path / "archive"
    compat = tmp_path / "archive" / "compat"
    src = root / "2024" / "09" / "VID_0003.MOV"
    src.parent.mkdir(parents=True)
    src.write_text("video", encoding="utf-8")

    outputs: list[Path] = []
    monkeypatch.setattr(convert, "probe_video_codec", lambda _p: "hevc")

    def fake_transcode(source: Path, output: Path, crf: int = 20) -> None:
        outputs.append(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("mp4", encoding="utf-8")

    monkeypatch.setattr(convert, "transcode_to_mp4", fake_transcode)

    summary = convert.convert_videos(root, compat, crf=22)

    assert summary.transcoded == 1
    # The mp4 mirrors the source's relative path under compat/, never alongside it.
    assert outputs == [compat / "2024" / "09" / "VID_0003.mp4"]
    assert not (src.parent / "VID_0003.mp4").exists()


def test_convert_videos_skips_live_motion_and_current_outputs(tmp_path: Path, monkeypatch):
    root = tmp_path / "archive"
    compat = root / "compat"
    root.mkdir()
    live_mov = root / "IMG_0001.MOV"
    live_still = root / "IMG_0001.HEIC"
    current_mov = root / "VID_0002.MOV"
    standalone = root / "VID_0003.MOV"
    non_hevc = root / "VID_0004.MOV"
    for path in (live_mov, live_still, current_mov, standalone, non_hevc):
        path.write_text(path.name, encoding="utf-8")
    # An already-current mp4 sits in compat/ and is newer than its source.
    current_mp4 = compat / "VID_0002.mp4"
    current_mp4.parent.mkdir(parents=True)
    current_mp4.write_text("mp4", encoding="utf-8")
    os.utime(current_mp4, (current_mov.stat().st_atime + 10, current_mov.stat().st_mtime + 10))

    transcoded: list[Path] = []

    def fake_codec(path: Path) -> str:
        return "h264" if path == non_hevc else "hevc"

    def fake_transcode(source: Path, output: Path, crf: int = 20) -> None:
        transcoded.append(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{source}:{crf}", encoding="utf-8")

    monkeypatch.setattr(convert, "probe_video_codec", fake_codec)
    monkeypatch.setattr(convert, "transcode_to_mp4", fake_transcode)

    summary = convert.convert_videos(root, compat, crf=22)

    # live motion + already-current + non-HEVC all fold into one skipped count.
    assert summary.skipped == 3
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
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("mp4", encoding="utf-8")

    monkeypatch.setattr(convert, "transcode_to_mp4", fake_transcode)

    summary = convert.convert_videos(tmp_path, tmp_path / "compat")

    assert summary.transcoded == 1
    assert transcoded == [archive_video]


def test_convert_videos_skips_non_hevc_without_a_cache(tmp_path: Path, monkeypatch):
    # No on-disk cache anymore: _is_output_current keeps repeat runs idempotent, and a
    # non-HEVC source is simply skipped each pass (transcodes nothing either way).
    root = tmp_path / "archive"
    root.mkdir()
    video = root / "VID_0001.MOV"
    video.write_text("video", encoding="utf-8")

    monkeypatch.setattr(convert, "probe_video_codec", lambda _path: "h264")

    first = convert.convert_videos(root, root / "compat")
    second = convert.convert_videos(root, root / "compat")

    assert first.skipped == 1
    assert first.transcoded == 0
    assert second.skipped == 1
    assert second.transcoded == 0


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
