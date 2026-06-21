"""Detect whether we're running inside the frozen PyInstaller .app bundle.

The shipped no-Terminal app is a PyInstaller ``--windowed`` bundle whose single signed
binary self-reinvokes (``--pyi-cli`` / ``--pyi-osxphotos``) so the CLI, osxphotos, and the
PhotoKit delete all run inside the app's own code signature (a clean "photos-tool" TCC
identity). This is the one place that knows how to recognize that frozen context.
"""

from __future__ import annotations

import os
import sys


def is_pyinstaller_bundle() -> bool:
    """True when running as the frozen PyInstaller app (which self-reinvokes for the CLI)."""
    return bool(getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None))


def bundled_exiftool_dir() -> str | None:
    """Directory holding the bundled exiftool inside the frozen app, else ``None``.

    The app ships its own exiftool (script + Perl lib tree, run via the system perl) so it works
    on a clean Mac with no Homebrew. In dev/CI (not frozen) this returns ``None`` so callers fall
    back to a PATH exiftool. The location must match the ``Tree(prefix=...)`` in the .spec.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    exe = os.path.join(base, "exiftool", "exiftool")
    return os.path.dirname(exe) if os.path.exists(exe) else None
