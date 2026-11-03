from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from photos_tool.convert import make_viewable_mp4, read_stamp

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.requires_sips
@pytest.mark.requires_exiftool
def test_real_sips_fixture_heic_to_jpeg_metadata_smoke(tmp_path: Path):
    sips = shutil.which("sips")
    exiftool = shutil.which("exiftool")
    if not sips or not exiftool:
        pytest.skip("sips and exiftool are required")

    source = tmp_path / "sample.heic"
    output = tmp_path / "sample.jpg"
    shutil.copy2(FIXTURES / "sample.heic", source)
    subprocess.run(
        [
            exiftool,
            "-overwrite_original",
            "-DateTimeOriginal=2024:01:02 03:04:05",
            "-GPSLatitude=61.2181",
            "-GPSLatitudeRef=N",
            "-GPSLongitude=149.9003",
            "-GPSLongitudeRef=W",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    subprocess.run(
        [sips, "-s", "format", "jpeg", str(source), "--out", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    metadata = _exif_json(
        exiftool,
        output,
        "-DateTimeOriginal",
        "-GPSLatitude",
        "-GPSLongitude",
    )

    assert metadata["DateTimeOriginal"] == "2024:01:02 03:04:05"
    assert "61" in str(metadata["GPSLatitude"])
    assert "149" in str(metadata["GPSLongitude"])


@pytest.mark.requires_avconvert
@pytest.mark.requires_exiftool
def test_real_avconvert_turns_iphone_hevc_into_windows_playable_h264(tmp_path: Path):
    # The "viewable" promise on real tools: an iPhone-style HEVC clip (10-bit, AAC, date/GPS/model
    # tags) becomes an H.264 MP4 with its tags restored and our identity stamped in.
    exiftool = _require_avconvert_and_exiftool()
    source = tmp_path / "clip.mov"
    shutil.copy2(FIXTURES / "sample_hevc.mov", source)
    output = tmp_path / "share" / "clip.mp4"

    make_viewable_mp4(source, output, identity="REAL-UUID-1")

    meta = _exif_json(
        exiftool,
        output,
        "-api",
        "QuickTimeUTC=1",
        "-FileType",
        "-CompressorID",
        "-ImageSize",
        "-CreateDate",
        "-GPSCoordinates",
        "-Model",
    )
    assert meta["FileType"] == "MP4"
    assert meta["CompressorID"] == "avc1"  # H.264: plays on a stock Windows PC
    assert meta["ImageSize"] == "160x96"  # source resolution kept
    assert "2024:08:05" in str(meta["CreateDate"])  # capture date, not the conversion time
    assert "61" in str(meta["GPSCoordinates"]) and "149" in str(meta["GPSCoordinates"])
    assert meta["Model"] == "iPhone 15 Pro"
    assert read_stamp(output)[0] == "REAL-UUID-1"
    assert not list(output.parent.glob("*.partial.mp4"))


@pytest.mark.requires_avconvert
@pytest.mark.requires_exiftool
def test_real_avconvert_repackages_h264_without_reencoding(tmp_path: Path):
    exiftool = _require_avconvert_and_exiftool()
    source = tmp_path / "old.mov"
    shutil.copy2(FIXTURES / "sample_h264.mov", source)
    output = tmp_path / "old.mp4"

    make_viewable_mp4(source, output)

    meta = _exif_json(exiftool, output, "-FileType", "-CompressorID", "-ImageSize")
    assert (meta["FileType"], meta["CompressorID"], meta["ImageSize"]) == ("MP4", "avc1", "160x96")


def _require_avconvert_and_exiftool() -> str:
    exiftool = shutil.which("exiftool")
    if not shutil.which("avconvert") or not exiftool:
        pytest.skip("avconvert (macOS) and exiftool are required")
    return exiftool


def _exif_json(exiftool: str, path: Path, *tags: str) -> dict[str, object]:
    result = subprocess.run(
        [exiftool, "-json", *tags, str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = json.loads(result.stdout)
    assert isinstance(rows, list)
    assert rows
    row = rows[0]
    assert isinstance(row, dict)
    return row
