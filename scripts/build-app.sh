#!/usr/bin/env bash
# Build, ad-hoc sign, and verify dist/photos-tool.app for single-Mac family use.
# Run from anywhere: ./scripts/build-app.sh
# Ad-hoc (no Apple Developer certificate) is intentional for a family handoff; a stable
# CFBundleIdentifier keeps the Photos/TCC grant attributed to "photos-tool" and reused.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
APP="$REPO/dist/photos-tool.app"
BUNDLE_ID="com.dominicreiss.photos-tool"

cd "$REPO"

echo "==> Ensuring py2app is available (build-only dep)"
"$PY" -c "import py2app" 2>/dev/null || "$PY" -m pip install -r requirements-build.txt

echo "==> Cleaning previous build"
rm -rf "$REPO/build" "$APP"

echo "==> Building app bundle with py2app"
"$PY" setup.py py2app

echo "==> Ad-hoc code signing (no certificate; single family Mac)"
# --deep re-signs every nested binary (embedded python, dylibs, pyobjc/osxphotos .so files);
# --force overwrites py2app's placeholder signatures. Sign AFTER py2app writes Info.plist;
# never edit the plist after this point or the identity changes.
codesign --force --deep --sign - "$APP"

echo "==> Verifying signature (structural integrity)"
codesign --verify --deep --strict --verbose=2 "$APP"
echo "==> Signature details (expect Identifier=$BUNDLE_ID, Signature=adhoc)"
codesign -dv --verbose=4 "$APP" 2>&1 | grep -E "Identifier|Signature|flags" || true

echo "==> Confirming key Info.plist values"
/usr/libexec/PlistBuddy -c "Print :LSUIElement" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Print :NSPhotoLibraryUsageDescription" "$APP/Contents/Info.plist" >/dev/null

echo "==> Embedded interpreter (must match a _cli_prefix candidate in menubar.py)"
# shellcheck disable=SC2012  # human-readable listing; filenames here are build-controlled
find "$APP/Contents/Frameworks/Python.framework/Versions" -maxdepth 2 -name "python3*" 2>/dev/null || true
find "$APP/Contents/MacOS" -maxdepth 1 2>/dev/null || true

echo
echo "NOTE: 'spctl -a -vv $APP' will report REJECTED (no notarization) -- that is EXPECTED"
echo "      for an ad-hoc app and does NOT mean the bundle is broken."
echo
echo "Built:  $APP"
echo "TCC id: $BUNDLE_ID"
echo "Next:   drag photos-tool.app to /Applications, right-click > Open on first run,"
echo "        add it to Login Items, and grant it Full Disk Access (then quit + relaunch)."
