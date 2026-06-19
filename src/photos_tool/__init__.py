"""photos-tool: push selected Apple Photos from a Mac to a Windows PC on the LAN."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version pip/uv installed (from pyproject), so
    # `photos-tool --version` can never drift from the published package.
    __version__ = version("photos-tool")
except PackageNotFoundError:  # pragma: no cover - only when running from a bare checkout
    __version__ = "0.0.0+unknown"
