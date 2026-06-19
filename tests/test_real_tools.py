from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from photos_tool.convert import transcode_to_mp4

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


@pytest.mark.requires_ffmpeg
@pytest.mark.requires_exiftool
def test_real_ffmpeg_transcode_preserves_quicktime_metadata(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    exiftool = shutil.which("exiftool")
    if not ffmpeg or not ffprobe or not exiftool:
        pytest.skip("ffmpeg, ffprobe, and exiftool are required")

    encoder = _hevc_encoder(ffmpeg)
    if not encoder:
        pytest.skip("ffmpeg has no available HEVC encoder")

    source = tmp_path / "source.mov"
    output = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=32x32:rate=1",
            "-c:v",
            encoder,
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        [
            exiftool,
            "-overwrite_original",
            "-QuickTime:CreateDate=2024:01:02 03:04:05",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    transcode_to_mp4(source, output, crf=28)

    codec = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    metadata = subprocess.run(
        [exiftool, "-s3", "-QuickTime:CreateDate", str(output)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert codec == "h264"
    assert "2024:01:02" in metadata


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


def _hevc_encoder(ffmpeg: str) -> str | None:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    )
    encoders = result.stdout
    if "libx265" in encoders:
        return "libx265"
    if "hevc_videotoolbox" in encoders:
        return "hevc_videotoolbox"
    return None
