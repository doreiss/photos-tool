"""Detect whether we're running inside the frozen PyInstaller .app bundle.

The shipped no-Terminal app is a PyInstaller ``--windowed`` bundle whose single signed
binary self-reinvokes (``--pyi-cli`` / ``--pyi-osxphotos``) so the CLI, osxphotos, and the
PhotoKit delete all run inside the app's own code signature (a clean "photos-tool" TCC
identity). This is the one place that knows how to recognize that frozen context.
"""

from __future__ import annotations

import sys


def is_pyinstaller_bundle() -> bool:
    """True when running as the frozen PyInstaller app (which self-reinvokes for the CLI)."""
    return bool(getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None))
