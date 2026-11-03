#!/usr/bin/env bash
# Package the built .app as a drag-to-Applications disk image for another Mac.
# Run after ./scripts/build-app.sh:   ./scripts/package-dmg.sh
#
# The DMG holds the signed app, an Applications shortcut to drag it onto, and the plain-language
# setup guide (docs/family-guide.md) as "Read Me First.txt". The app's signature travels intact,
# so the recipient's macOS grants persist across future updates built with the same cert.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$REPO/dist/photos-tool.app"
DMG="$REPO/dist/photos-tool.dmg"
GUIDE="$REPO/docs/family-guide.md"

[ -d "$APP" ] || { echo "ERROR: $APP not found; run ./scripts/build-app.sh first" >&2; exit 1; }
codesign --verify --deep --strict "$APP" \
  || { echo "ERROR: $APP fails signature verification; rebuild it" >&2; exit 1; }

STAGE="$(mktemp -d)"
MNT=""
cleanup() {
  [ -n "$MNT" ] && hdiutil detach -quiet "$MNT" 2>/dev/null || true
  rm -rf "$STAGE"
}
trap cleanup EXIT

echo "==> Staging"
ditto "$APP" "$STAGE/photos-tool.app"  # ditto preserves the bundle's signature + metadata
ln -s /Applications "$STAGE/Applications"
cp "$GUIDE" "$STAGE/Read Me First.txt"

echo "==> Creating $DMG"
rm -f "$DMG"
hdiutil create -quiet -volname "photos-tool" -srcfolder "$STAGE" -fs HFS+ -format UDZO "$DMG"

echo "==> Verifying the app inside the image"
MNT="$(mktemp -d)"
hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MNT" "$DMG"
codesign --verify --deep --strict "$MNT/photos-tool.app"
dr_info="$(codesign -d -r- "$MNT/photos-tool.app" 2>&1)"
case "$dr_info" in
  *"certificate leaf"*) echo "  signature intact; stable identity (grants will persist)" ;;
  *) echo "  WARNING: ad-hoc signed — the recipient will re-grant after every update" >&2 ;;
esac

echo "Built:  $DMG ($(du -h "$DMG" | cut -f1))"
echo "Send it by AirDrop, or copy it to the shared folder. Setup steps are inside the image."
