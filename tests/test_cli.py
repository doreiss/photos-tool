from __future__ import annotations

import pytest

from photos_tool import cli, tooling


def test_plan_default_prints_selected_export(capsys):
    rc = cli.main(["plan", "/Volumes/Family"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("osxphotos export /Volumes/Family --selected")


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


def test_no_subcommand_errors():
    with pytest.raises(SystemExit):
        cli.main([])
