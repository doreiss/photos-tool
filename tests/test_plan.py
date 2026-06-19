from __future__ import annotations

import pytest

from photos_tool.plan import ExportOptions, build_export_command


def test_default_selected_command():
    cmd = build_export_command(ExportOptions(destination="/Volumes/Family"))
    assert cmd[:3] == ["osxphotos", "export", "/Volumes/Family"]
    for flag in ("--selected", "--update", "--exiftool", "--download-missing", "--touch-file"):
        assert flag in cmd
    assert cmd[cmd.index("--directory") + 1] == "{created.year}/{created.mm}"
    assert cmd[cmd.index("--retry") + 1] == "3"


def test_album_scope_uses_album_not_selected():
    cmd = build_export_command(ExportOptions(destination="/d", scope="album", album="Summer Trip"))
    assert cmd[cmd.index("--album") + 1] == "Summer Trip"
    assert "--selected" not in cmd


def test_album_scope_requires_a_name():
    with pytest.raises(ValueError):
        build_export_command(ExportOptions(destination="/d", scope="album", album=None))


def test_jpeg_copies_use_quality():
    cmd = build_export_command(
        ExportOptions(destination="/d", convert_to_jpeg=True, jpeg_quality=0.85)
    )
    assert "--convert-to-jpeg" in cmd
    assert cmd[cmd.index("--jpeg-quality") + 1] == "0.85"


def test_destination_is_required():
    with pytest.raises(ValueError):
        build_export_command(ExportOptions(destination=""))


def test_retry_omitted_when_zero():
    cmd = build_export_command(ExportOptions(destination="/d", retry=0))
    assert "--retry" not in cmd
