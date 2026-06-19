from __future__ import annotations

from photos_tool.tooling import Tool, ToolStatus, missing_required, probe


def test_probe_reports_missing_when_not_on_path():
    tool = Tool("definitely-not-installed", required=True, purpose="x")
    status = probe(tool, which=lambda _name: None)
    assert not status.found
    assert status.version is None


def test_missing_required_ignores_optional_tools():
    required = Tool("a", required=True, purpose="x")
    optional = Tool("b", required=False, purpose="y")
    statuses = [
        ToolStatus(tool=required, path=None, version=None),
        ToolStatus(tool=optional, path=None, version=None),
    ]
    assert [status.tool.name for status in missing_required(statuses)] == ["a"]
