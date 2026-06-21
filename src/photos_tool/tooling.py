"""Detection of the external command-line tools the wrapper relies on.

The wrapper shells out to these tools rather than importing their Python APIs, so
this module is the single place that knows what must be installed and reports it
clearly before any export runs.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    """An external command-line dependency."""

    name: str
    required: bool
    purpose: str
    version_args: tuple[str, ...] = ("--version",)


# osxphotos and exiftool are mandatory; ffmpeg is only needed for optional MP4 copies.
REQUIRED_TOOLS: tuple[Tool, ...] = (
    Tool("osxphotos", required=True, purpose="export selected photos from the Photos library"),
    Tool(
        "exiftool",
        required=True,
        purpose="embed and verify EXIF, GPS and date metadata",
        version_args=("-ver",),  # exiftool prints just the version number for -ver
    ),
    Tool("ffmpeg", required=False, purpose="transcode HEVC video to H.264 MP4 (optional copies)"),
    Tool("ffprobe", required=False, purpose="detect source video codec for optional MP4 copies"),
)


@dataclass(frozen=True)
class ToolStatus:
    """The result of probing for a single tool."""

    tool: Tool
    path: str | None
    version: str | None

    @property
    def found(self) -> bool:
        return self.path is not None


def _query_version(argv: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            [*argv],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0].strip() if text else None


def probe(tool: Tool, which: Callable[[str], str | None] = shutil.which) -> ToolStatus:
    """Locate ``tool`` and capture its version string if present.

    osxphotos has no binary on PATH inside the frozen .app bundle — it runs via the app's
    own binary (self-reinvocation). Probe it through that same path so the bundle reports it
    found (and ``send``'s preflight doesn't wrongly block on it).
    """
    if tool.name == "osxphotos":
        from .osxphotos_runner import _osxphotos_argv

        argv = _osxphotos_argv([tool.name, *tool.version_args])
        if argv[0] != tool.name:  # frozen bundle: [app-exe, --pyi-osxphotos/-m, --version]
            return ToolStatus(tool=tool, path=argv[0], version=_query_version(argv))

    path = which(tool.name)
    version = _query_version([path, *tool.version_args]) if path else None
    return ToolStatus(tool=tool, path=path, version=version)


def probe_all(
    tools: Sequence[Tool] = REQUIRED_TOOLS,
    which: Callable[[str], str | None] = shutil.which,
) -> list[ToolStatus]:
    return [probe(tool, which=which) for tool in tools]


def missing_required(statuses: Sequence[ToolStatus]) -> list[ToolStatus]:
    """Return the statuses for required tools that were not found."""
    return [status for status in statuses if status.tool.required and not status.found]
