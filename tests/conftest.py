from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


@dataclass(frozen=True)
class FakeTools:
    scenario_path: Path
    log_path: Path
    state_path: Path
    bin_dir: Path

    def log(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


@pytest.fixture
def fake_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def install(scenario: dict[str, Any]) -> FakeTools:
        scenario_path = tmp_path / "scenario.json"
        log_path = tmp_path / "tools.jsonl"
        state_path = tmp_path / "state.json"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
        fakebin = Path(__file__).parent / "fakebin" / "tool.py"
        for name in ("osxphotos", "exiftool", "ffmpeg", "ffprobe", "osascript", "mount"):
            shim = bin_dir / name
            shim.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "import runpy\n"
                "import sys\n"
                f"os.environ['PHOTOS_TOOL_FAKE_NAME'] = {name!r}\n"
                f"sys.argv[0] = {str(shim)!r}\n"
                f"runpy.run_path({str(fakebin)!r}, run_name='__main__')\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
        monkeypatch.setenv("PHOTOS_TOOL_FAKE_SCENARIO", str(scenario_path))
        monkeypatch.setenv("PHOTOS_TOOL_FAKE_LOG", str(log_path))
        monkeypatch.setenv("PHOTOS_TOOL_FAKE_STATE", str(state_path))
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        return FakeTools(
            scenario_path=scenario_path,
            log_path=log_path,
            state_path=state_path,
            bin_dir=bin_dir,
        )

    return install
