"""py2app build entry point for the photos-tool menu-bar agent.

This is NOT the runtime entry point — runtime entry points stay in pyproject.toml
[project.scripts]. This file exists only so ``python setup.py py2app`` can produce
dist/photos-tool.app.

Build (in the project venv, on Apple Silicon):
    .venv/bin/python -m pip install -r requirements-build.txt
    .venv/bin/python setup.py py2app          # full bundle in ./dist/photos-tool.app

The produced bundle MUST then be ad-hoc code-signed (scripts/build-app.sh does this)
with a STABLE CFBundleIdentifier so its Photos/TCC grant is attributed to "photos-tool"
and reused across launches. Never edit Info.plist after signing.
"""

from importlib.metadata import PackageNotFoundError, version

from setuptools import setup

try:  # single source of truth: the installed package version (from pyproject.toml)
    _VERSION = version("photos-tool")
except PackageNotFoundError:  # not installed in the build env — fall back to the pin
    _VERSION = "0.0.1"

APP = ["app_main.py"]

# Nothing extra is bundled: config, state, the export DB, and reports all stay in $HOME
# and are never copied into the bundle (no secrets, no DB, no reports inside the app).
DATA_FILES: list = []

PLIST = {
    "CFBundleName": "photos-tool",
    "CFBundleDisplayName": "photos-tool",  # the name the Photos consent prompt shows
    # STABLE TCC identity — TCC keys the grant to this id + the signature. NEVER change it
    # once a family member has granted, or they re-grant on every launch.
    "CFBundleIdentifier": "com.dominicreiss.photos-tool",
    "CFBundleShortVersionString": _VERSION,
    "CFBundleVersion": _VERSION,
    "LSUIElement": True,  # menu-bar-only agent: no Dock icon, not in Cmd-Tab
    "LSMinimumSystemVersion": "13.0",
    "NSHighResolutionCapable": True,
    # Required or PhotoKit read auth is silently denied; shown on the read prompt.
    "NSPhotoLibraryUsageDescription": (
        "photos-tool reads the photos you select so it can copy them to your backup "
        "share with their dates and metadata intact."
    ),
    # Required for the read-write access level used by the recoverable cleanup delete.
    "NSPhotoLibraryAddUsageDescription": (
        "photos-tool moves originals you have already backed up into Recently Deleted "
        "(recoverable for 30 days) so they can be cleared from this Mac."
    ),
    "NSHumanReadableCopyright": "Copyright (c) 2026 Dominic Reiss. MIT License.",
}

OPTIONS = {
    # MUST be False for a menu-bar app: argv_emulation injects a Carbon event loop (for
    # file-drop droplets) that breaks the rumps/AppKit run loop.
    "argv_emulation": False,
    # Self-contained: embed our own Python.framework so the bundle does not depend on the
    # dev venv, and so the framework interpreter the app shells out to exists in the bundle.
    "semi_standalone": False,
    "site_packages": False,
    "arch": "arm64",  # CI + the family Mac are Apple Silicon.
    "optimize": 0,  # keep asserts; the safety gates are real if/raise but be conservative
    # Whole packages copied verbatim (preserving non-.py data files); these are the ones
    # py2app's static scanner under-detects or that ship data (osxphotos ships .mako/.tx/...).
    "packages": [
        "photos_tool",
        "rumps",
        "osxphotos",
        "certifi",
        "rich",
        "click",
        "mako",
        "markdown2",
        "yaml",
        "bpylist2",
        "photoscript",
        "osxmetadata",
        "cgmetadata",
        "textx",
        "whenever",
    ],
    # Individual modules py2app misses because they are imported lazily / inside functions
    # (the pyobjc framework bindings, especially ``import Photos`` in remove.py).
    "includes": [
        "Photos",
        "CoreFoundation",
        "Foundation",
        "AppKit",
        "Quartz",
        "AVFoundation",
        "objc",
        "pkg_resources",
    ],
    "excludes": ["tkinter", "test", "lib2to3", "pydoc_data"],
    "plist": PLIST,
}

setup(
    app=APP,
    name="photos-tool",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
