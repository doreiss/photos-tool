from __future__ import annotations

import os
from pathlib import Path

import pytest

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


# --- the "original" format's compat/ mirror ---------------------------------------------------


def _fake_make(made: list[tuple[Path, Path]]):
    def fake(source: Path, output: Path, **_kw: object) -> None:
        made.append((source, output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("mp4", encoding="utf-8")

    return fake


def test_convert_videos_writes_mp4_into_compat_tree(tmp_path: Path, monkeypatch):
    root = tmp_path / "archive"
    compat = root / "compat"
    src = root / "2024" / "09" / "VID_0003.MOV"
    src.parent.mkdir(parents=True)
    src.write_text("video", encoding="utf-8")
    made: list[tuple[Path, Path]] = []
    monkeypatch.setattr(convert, "probe_video_codec", lambda _p: "hvc1")
    monkeypatch.setattr(convert, "make_viewable_mp4", _fake_make(made))

    summary = convert.convert_videos(root, compat)

    assert summary.transcoded == 1
    # The mp4 mirrors the source's relative path under compat/, never alongside it.
    assert made == [(src, compat / "2024" / "09" / "VID_0003.mp4")]
    assert not (src.parent / "VID_0003.mp4").exists()


def test_convert_videos_skips_live_motion_current_outputs_and_non_hevc(tmp_path: Path, monkeypatch):
    root = tmp_path / "archive"
    compat = root / "compat"
    root.mkdir()
    live_mov = root / "IMG_0001.MOV"
    live_still = root / "IMG_0001.HEIC"
    current_mov = root / "VID_0002.MOV"
    standalone = root / "VID_0003.MOV"
    already_h264 = root / "VID_0004.MOV"
    for path in (live_mov, live_still, current_mov, standalone, already_h264):
        path.write_text(path.name, encoding="utf-8")
    current_mp4 = compat / "VID_0002.mp4"  # newer than its source -> current
    current_mp4.parent.mkdir(parents=True)
    current_mp4.write_text("mp4", encoding="utf-8")
    os.utime(current_mp4, (current_mov.stat().st_atime + 10, current_mov.stat().st_mtime + 10))
    made: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        convert, "probe_video_codec", lambda p: "avc1" if p == already_h264 else "hvc1"
    )
    monkeypatch.setattr(convert, "make_viewable_mp4", _fake_make(made))

    summary = convert.convert_videos(root, compat)

    # live motion + already-current + already-H.264 all fold into one skipped count.
    assert summary.skipped == 3
    assert summary.transcoded == 1
    assert [source for source, _ in made] == [standalone]


def test_convert_videos_excludes_compat_tree(tmp_path: Path, monkeypatch):
    archive_video = tmp_path / "2024" / "01" / "VID_0001.MOV"
    compat_video = tmp_path / "compat" / "2024" / "01" / "VID_0001.MOV"
    for path in (archive_video, compat_video):
        path.parent.mkdir(parents=True)
        path.write_text(path.name, encoding="utf-8")
    made: list[tuple[Path, Path]] = []
    monkeypatch.setattr(convert, "probe_video_codec", lambda _p: "hvc1")
    monkeypatch.setattr(convert, "make_viewable_mp4", _fake_make(made))

    summary = convert.convert_videos(tmp_path, tmp_path / "compat")

    assert summary.transcoded == 1
    assert [source for source, _ in made] == [archive_video]


def test_find_video_candidates_maps_walk_oserror(tmp_path: Path, monkeypatch):
    def broken_rglob(self: Path, _pattern: str):
        if self == tmp_path:
            raise OSError("network dropped")
        return ()

    monkeypatch.setattr(Path, "rglob", broken_rglob)

    with pytest.raises(ConversionError, match="network dropped"):
        convert.find_video_candidates(tmp_path)


# --- make_viewable_mp4 (avconvert + exiftool, via the fake tools on PATH) -----------------------


def _avconvert_presets(tools) -> list[str]:
    return [
        e["argv"][e["argv"].index("--preset") + 1] for e in tools.log() if e["tool"] == "avconvert"
    ]


def _source(tmp_path: Path, name: str) -> Path:
    source = tmp_path / "staging" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source-video")
    return source


def test_hevc_is_reencoded_to_h264_with_tags_identity_and_source_mtime(tmp_path, fake_tools):
    tools = fake_tools({"codecs": {"clip.mov": "hvc1"}})
    source = _source(tmp_path, "clip.mov")
    os.utime(source, (1_700_000_000, 1_700_000_000))
    output = tmp_path / "share" / "2024" / "09" / "clip.mp4"

    convert.make_viewable_mp4(source, output, identity="UUID-1")

    assert output.exists()
    assert _avconvert_presets(tools) == ["PresetHighestQuality"]  # HEVC: never passthrough
    tag_calls = [e["argv"] for e in tools.log() if "-tagsFromFile" in e["argv"]]
    assert tag_calls and tag_calls[0][tag_calls[0].index("-tagsFromFile") + 1] == str(source)
    assert convert.read_stamp(output)[0] == "UUID-1"
    assert output.stat().st_mtime == source.stat().st_mtime  # dated like the photo, not "now"
    assert list(output.parent.glob("*.partial.mp4")) == []


def test_h264_is_repackaged_losslessly_first(tmp_path, fake_tools):
    tools = fake_tools({"codecs": {"old.mov": "avc1"}})
    output = tmp_path / "share" / "old.mp4"

    convert.make_viewable_mp4(_source(tmp_path, "old.mov"), output)

    assert _avconvert_presets(tools) == ["PresetPassthrough"]
    assert "PresetPassthrough" in output.read_text(encoding="utf-8")


def test_failed_passthrough_falls_back_to_reencode(tmp_path, fake_tools):
    tools = fake_tools({"codecs": {"odd.mov": "avc1"}, "passthrough_fail": ["odd.mov"]})
    output = tmp_path / "share" / "odd.mp4"

    convert.make_viewable_mp4(_source(tmp_path, "odd.mov"), output)

    assert _avconvert_presets(tools) == ["PresetPassthrough", "PresetHighestQuality"]
    assert output.exists()


@pytest.mark.parametrize(
    "scenario",
    [
        {"avconvert_fail": True},  # the encoder itself fails
        {"exiftool_tag_fail": True},  # converted, but the metadata copy fails
        {"output_codec": "hvc1"},  # avconvert "succeeds" but emits something Windows can't play
    ],
    ids=["avconvert-fails", "tag-copy-fails", "not-h264"],
)
def test_any_failure_publishes_nothing(tmp_path, fake_tools, scenario):
    # A half-made or unplayable MP4 must never appear at the output path: the compat mirror would
    # treat it as current forever, and in "viewable" the deletion gate would trust it.
    fake_tools(scenario)
    source = _source(tmp_path, "clip.mov")
    output = tmp_path / "share" / "clip.mp4"

    with pytest.raises(ConversionError):
        convert.make_viewable_mp4(source, output, identity="UUID-1")

    assert not output.exists()
    assert convert._is_output_current(source, output) is False
    assert list(output.parent.glob("*.partial.mp4")) == []


def test_read_stamp_is_empty_for_files_we_did_not_make(tmp_path, fake_tools):
    fake_tools({})
    foreign = tmp_path / "someone-elses.mp4"
    foreign.write_text("mp4 from elsewhere\n", encoding="utf-8")

    assert convert.read_stamp(foreign)[0] is None
    assert convert.read_stamp(tmp_path / "missing.mp4")[0] is None


def test_stamp_carries_identity_and_source_signature(tmp_path, fake_tools):
    fake_tools({})
    output = tmp_path / "share" / "clip.mp4"

    convert.make_viewable_mp4(
        _source(tmp_path, "clip.mov"), output, identity="UUID-1", signature="123:abc"
    )

    assert convert.read_stamp(output) == ("UUID-1", "123:abc")


def test_unwritable_destination_is_a_conversion_error_not_a_crash(tmp_path, fake_tools):
    # e.g. the share dropped: the folder can't be created. Must surface as ConversionError so the
    # video pass marks just that video failed instead of the whole send dying with a traceback.
    fake_tools({})
    blocker = tmp_path / "share"
    blocker.write_text("a file where a folder should be", encoding="utf-8")

    with pytest.raises(ConversionError):
        convert.make_viewable_mp4(_source(tmp_path, "clip.mov"), blocker / "2024" / "clip.mp4")
