# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the no-Terminal photos-tool menu-bar .app (family delivery).

Build via ``scripts/build-app.sh`` (which uses the framework build venv ``.venv-app``).
The frozen app is a single signed binary that self-reinvokes (``--pyi-cli`` /
``--pyi-osxphotos``) so osxphotos and the PhotoKit delete run inside the app's own code
signature — a clean "photos-tool" TCC identity. A real Mach-O launcher (no exec swap) is
why the menu-bar status item draws; standalone py2app could not bundle osxphotos's
~90-package tree (see docs/app-delivery-findings.md).
"""

import os
from importlib.metadata import PackageNotFoundError, version

from PyInstaller.utils.hooks import collect_all, collect_submodules

try:
    _VERSION = version("photos-tool")
except PackageNotFoundError:  # not installed in the build env — fall back to the pin
    _VERSION = "0.0.1"

# Repo-relative so the build works on any machine (SPECPATH is this spec's directory).
_SRC = os.path.normpath(os.path.join(SPECPATH, "..", "src"))

datas, binaries, hiddenimports = [], [], []
# osxphotos shells out are never imported by photos_tool, so PyInstaller can't discover the
# tree from our code — collect osxphotos + the packages it lazily imports in full.
for pkg in ("osxphotos", "rumps", "photoscript", "osxmetadata", "utitools"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("bitstring")
hiddenimports += [
    "Photos",
    "objc",
    "Foundation",
    "AppKit",
    "Quartz",
    "AVFoundation",
    "CoreFoundation",
]

a = Analysis(
    [os.path.join(SPECPATH, "app_main.py")],
    pathex=[_SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="photos-tool",
    console=False,
    target_arch="arm64",
    codesign_identity=None,  # ad-hoc signed by scripts/build-app.sh after the build
)
coll = COLLECT(exe, a.binaries, a.datas, name="photos-tool")
app = BUNDLE(
    coll,
    name="photos-tool.app",
    icon=None,
    # STABLE id: TCC keys the Photos/Automation/FDA grants to this. Never change it.
    bundle_identifier="com.dominicreiss.photos-tool",
    info_plist={
        "CFBundleName": "photos-tool",
        "CFBundleDisplayName": "photos-tool",
        "CFBundleShortVersionString": _VERSION,
        "CFBundleVersion": _VERSION,
        "LSUIElement": True,  # menu-bar-only agent (no Dock icon)
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        # LOAD-BEARING: without this key macOS's tccd refuses the Apple Events request
        # outright (no consent dialog, no entry in System Settings > Automation), so the
        # app can never read the live Photos selection. See request_photos_automation().
        "NSAppleEventsUsageDescription": (
            "photos-tool reads which photos you have selected in Photos so it can back "
            "them up."
        ),
        "NSPhotoLibraryUsageDescription": (
            "photos-tool reads the photos you select so it can copy them to your backup "
            "share with their dates and metadata intact."
        ),
        "NSPhotoLibraryAddUsageDescription": (
            "photos-tool moves originals you have already backed up into Recently Deleted "
            "(recoverable for 30 days) so they can be cleared from this Mac."
        ),
    },
)
