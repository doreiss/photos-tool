from __future__ import annotations

import os

import pytest

from photos_tool import cli, tooling


def test_plan_default_prints_selected_export(capsys):
    rc = cli.main(["plan", "/Volumes/Family"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("osxphotos export /Volumes/Family --selected")
    assert "--exportdb" in out
    assert ".osxphotos_export.db" in out


def test_plan_album(capsys):
    rc = cli.main(["plan", "/d", "--album", "Trip"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--album Trip" in out
    assert "--selected" not in out


def test_check_passes_when_all_present(monkeypatch, capsys):
    fake = [
        tooling.ToolStatus(tool=tool, path=f"/usr/bin/{tool.name}", version="1.0")
        for tool in tooling.REQUIRED_TOOLS
    ]
    monkeypatch.setattr(cli, "probe_all", lambda: fake)
    assert cli.main(["check"]) == 0
    assert "All required tools present" in capsys.readouterr().out


def test_check_fails_when_required_missing(monkeypatch):
    fake = [
        tooling.ToolStatus(tool=tool, path=None, version=None) for tool in tooling.REQUIRED_TOOLS
    ]
    monkeypatch.setattr(cli, "probe_all", lambda: fake)
    assert cli.main(["check"]) == 1


def test_init_non_interactive_writes_config_without_secrets(tmp_path, capsys):
    path = tmp_path / "config.toml"

    rc = cli.main(
        [
            "init",
            "--non-interactive",
            "--config",
            str(path),
            "--smb-url",
            "smb://192.168.1.50/FamilyPhotos",
            "--mount-point",
            "/Volumes/FamilyPhotos",
            "--jpeg",
        ]
    )
    captured = capsys.readouterr()
    text = path.read_text(encoding="utf-8")

    assert rc == 0
    assert "Wrote" in captured.out
    assert "install-shortcut" in captured.out
    assert 'smb_url = "smb://192.168.1.50/FamilyPhotos"' in text
    assert 'mount_point = "/Volumes/FamilyPhotos"' in text
    assert "jpeg = true" in text
    assert "password" not in text.lower()


def test_install_shortcut_writes_executable_launcher(tmp_path, capsys):
    script = tmp_path / "send-selected.sh"
    config = tmp_path / "config.toml"

    rc = cli.main(["install-shortcut", "--script", str(script), "--config", str(config)])
    captured = capsys.readouterr()
    text = script.read_text(encoding="utf-8")

    assert rc == 0
    assert "Run Shell Script" in captured.out
    assert os.access(script, os.X_OK)
    assert "photos-tool send" in text
    assert f"--config {config}" in text
    assert "password" not in text.lower()


def test_install_shortcut_refuses_overwrite_without_force(tmp_path):
    script = tmp_path / "send-selected.sh"
    script.write_text("keep", encoding="utf-8")

    assert cli.main(["install-shortcut", "--script", str(script)]) == 2
    assert script.read_text(encoding="utf-8") == "keep"


def test_sanitize_report_command_writes_privacy_preserving_copy(tmp_path, capsys):
    source = tmp_path / "raw.json"
    target = tmp_path / "sanitized.json"
    source.write_text(
        '[{"uuid":"real-uuid","filename":"/Users/example/Pictures/IMG_0001.HEIC"}]',
        encoding="utf-8",
    )

    rc = cli.main(["sanitize-report", str(source), str(target)])
    captured = capsys.readouterr()
    text = target.read_text(encoding="utf-8")

    assert rc == 0
    assert "Wrote sanitized report" in captured.out
    assert "real-uuid" not in text
    assert "/Users/example" not in text


def test_sanitize_report_command_refuses_overwrite_without_force(tmp_path):
    source = tmp_path / "raw.json"
    target = tmp_path / "sanitized.json"
    source.write_text("[]", encoding="utf-8")
    target.write_text("keep", encoding="utf-8")

    assert cli.main(["sanitize-report", str(source), str(target)]) == 2
    assert target.read_text(encoding="utf-8") == "keep"


def test_no_subcommand_errors():
    with pytest.raises(SystemExit):
        cli.main([])
