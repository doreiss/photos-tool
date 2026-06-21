from __future__ import annotations

from pathlib import Path

import pytest

from photos_tool.config import ConfigError, load_config, resolved_exportdb_path


def test_missing_config_uses_defaults(tmp_path: Path):
    config = load_config(tmp_path / "missing.toml")
    assert config.export.directory_template == "{created.year}/{created.mm}"
    assert config.copies.jpeg is False


def test_loads_sample_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[destination]
smb_url = "smb://192.168.1.50/FamilyPhotos"
mount_point = "/Volumes/FamilyPhotos"
subpath = "incoming"

[export]
directory_template = "{folder_album}"
filename_template = "{original_name}"
download_missing = false
use_photokit = true
retry = 5

[copies]
jpeg = true
jpeg_quality = 0.85
mp4 = true
mp4_crf = 23

[state]
exportdb_dir = "~/state/exportdb"
log_dir = "~/state/logs"
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.destination.smb_url == "smb://192.168.1.50/FamilyPhotos"
    assert config.destination_path() == Path("/Volumes/FamilyPhotos/incoming")
    assert config.export.directory_template == "{folder_album}"
    assert config.export.download_missing is False
    assert config.export.use_photokit is True
    assert config.export.retry == 5
    assert config.copies.jpeg is True
    assert config.copies.jpeg_quality == 0.85
    assert config.copies.mp4 is True
    assert config.copies.mp4_crf == 23


def test_config_rejects_bad_types(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[copies]\njpeg_quality = "high"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="jpeg_quality"):
        load_config(path)


def test_config_rejects_smb_url_with_credentials(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[destination]\nsmb_url = "smb://photos:secret@pc/FamilyPhotos"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="must not contain credentials"):
        load_config(path)


def test_config_rejects_smb_url_path_traversal(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[destination]\nsmb_url = "smb://pc/../evil"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid share path segment"):
        load_config(path)


def test_config_rejects_non_smb_url(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[destination]\nsmb_url = "https://pc/FamilyPhotos"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="smb://server/Share"):
        load_config(path)


def test_exportdb_path_is_stable_and_local(tmp_path: Path):
    first = resolved_exportdb_path("/Volumes/FamilyPhotos", tmp_path)
    second = resolved_exportdb_path("/Volumes/FamilyPhotos", tmp_path)

    assert first == second
    assert first.parent == tmp_path
    assert first.name.endswith(".osxphotos_export.db")
