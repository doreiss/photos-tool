#!/usr/bin/env bash
# Build, ad-hoc sign, and verify the no-Terminal photos-tool menu-bar .app via PyInstaller.
# Run from anywhere:  ./scripts/build-app.sh [--install]
#   --install   also copy the built app to /Applications
#
# The frozen app is one signed binary that self-reinvokes (--pyi-cli / --pyi-osxphotos), so
# osxphotos and the PhotoKit delete run inside the app's own code signature (clean
# "photos-tool" TCC identity). Ad-hoc signing has no Apple certificate: `spctl -a -vv` will
# report REJECTED and the first launch needs a one-time right-click > Open. (Standalone py2app
# was rejected — it can't bundle osxphotos's ~90-package tree; see docs/app-delivery-findings.md.)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Build venv with pyinstaller + the editable package + osxphotos/pyobjc. A framework Python
# (Homebrew python@3.11) makes the most portable bundle; overridable for CI.
PY="${PHOTOS_TOOL_BUILD_PYTHON:-$REPO/.venv-app/bin/python}"
SPEC="$REPO/packaging/photos-tool.spec"
APP="$REPO/dist/photos-tool.app"
BUNDLE_ID="com.dominicreiss.photos-tool"
INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

cd "$REPO"

if [ ! -x "$PY" ]; then
  echo "ERROR: build venv not found at $PY. Create it once:" >&2
  echo "  /opt/homebrew/opt/python@3.11/bin/python3.11 -m venv .venv-app" >&2
  echo "  .venv-app/bin/python -m pip install -e '.[gui]' -r requirements-build.txt" >&2
  exit 1
fi
if ! "$PY" -c "import sysconfig,sys; sys.exit(0 if sysconfig.get_config_var('PYTHONFRAMEWORK') else 1)"; then
  echo "WARNING: $PY is not a framework build; the bundle should still work, but Homebrew" >&2
  echo "         python@3.11 is recommended for the most portable .app." >&2
fi

echo "==> Ensuring the build toolchain is present"
"$PY" -c "import PyInstaller" 2>/dev/null || "$PY" -m pip install -r requirements-build.txt
"$PY" -c "import photos_tool" 2>/dev/null || "$PY" -m pip install -e '.[gui]'

echo "==> Cleaning previous build"
rm -rf "$REPO/build" "$APP"

echo "==> Building with PyInstaller"
"$PY" -m PyInstaller --noconfirm --log-level WARN "$SPEC"

echo "==> Ad-hoc code signing"
codesign --force --deep --sign - "$APP"

echo "==> Verifying signature + bundle shape"
codesign --verify --deep --strict --verbose=2 "$APP"
if codesign -dv --verbose=2 "$APP" 2>&1 | grep -q "Identifier=$BUNDLE_ID"; then
  echo "  signed as $BUNDLE_ID (ad-hoc)"
else
  echo "  WARNING: signed identifier is not $BUNDLE_ID" >&2
fi
EXE="$APP/Contents/MacOS/photos-tool"
if ! file "$EXE" | grep -q "Mach-O"; then
  echo "ERROR: launcher is not a Mach-O binary (the icon would not draw)" >&2
  exit 1
fi
lipo -archs "$EXE" 2>/dev/null | grep -q arm64 || echo "  WARNING: launcher is not arm64"
# Exhaustive Info.plist gate: a missing NSAppleEventsUsageDescription makes tccd SILENTLY
# refuse the Apple Events request (no prompt, no Automation-pane entry) — the bug that cost
# days. Verify every load-bearing key on the EMITTED artifact, not just two of them.
PLIST="$APP/Contents/Info.plist"
plutil -lint "$PLIST" >/dev/null  # structurally valid plist
for key in NSAppleEventsUsageDescription NSPhotoLibraryUsageDescription \
           NSPhotoLibraryAddUsageDescription LSUIElement CFBundleIdentifier; do
  if ! /usr/libexec/PlistBuddy -c "Print :$key" "$PLIST" >/dev/null 2>&1; then
    echo "ERROR: Info.plist missing $key (TCC would silently refuse the grant)" >&2
    exit 1
  fi
done

echo "==> Smoke-testing self-reinvocation (proves osxphotos resolves inside the bundle)"
"$EXE" --pyi-cli --version
# Capture each producer to a variable (runs it to completion, no early pipe reader) rather than
# piping to head/grep, which close the read end and can SIGPIPE the producer under `pipefail`.
osx_version="$("$EXE" --pyi-osxphotos --version)"
printf '  osxphotos: %s\n' "${osx_version%%$'\n'*}"
# Side-effect-free: proves AppKit/Foundation/ctypes + the CoreServices consent symbol all
# survived PyInstaller collection (what request_photos_automation needs at runtime), with NO
# consent dialog. Do NOT smoke --pyi-prime-photos here — it pops a prompt and hangs the build.
prime_out="$("$EXE" --pyi-prime-imports)"
case "$prime_out" in
  *"pyi-prime-imports OK"*) ;;
  *) echo "ERROR: --pyi-prime-imports smoke failed: $prime_out" >&2; exit 1 ;;
esac

if [ "$INSTALL" = "1" ]; then
  echo "==> Installing to /Applications"
  rm -rf /Applications/photos-tool.app
  cp -R "$APP" /Applications/photos-tool.app
  echo "  installed /Applications/photos-tool.app"
fi

echo
echo "NOTE: 'spctl -a -vv $APP' reports REJECTED (ad-hoc, no notarization) — that is EXPECTED."
echo "Built:  $APP"
echo "Next:   drag to /Applications, right-click > Open once, add to Login Items, and grant"
echo "        Full Disk Access, then Automation->Photos (first Send) and Photos (first cleanup)."
