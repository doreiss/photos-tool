#!/usr/bin/env bash
# Create the stable self-signed code-signing identity "photos-tool Self-Signed" ONCE, so the
# .app's TCC grants (Full Disk Access, Automation->Photos, Photos) survive rebuilds instead of
# re-prompting on every reinstall. After this, scripts/build-app.sh auto-signs with it.
#
# This needs your macOS login/keychain password once (to authorize codesign to use the new key).
# It does NOT make the app notarized — `spctl` still reports REJECTED, which is expected.
#
# After the FIRST build signed with this cert, run the one-time grant migration:
#   tccutil reset SystemPolicyAllFiles com.dominicreiss.photos-tool
#   tccutil reset AppleEvents          com.dominicreiss.photos-tool
#   tccutil reset Photos               com.dominicreiss.photos-tool
# then re-grant once. Every future rebuild keeps the grants (don't regenerate or rename the cert).
set -euo pipefail

CN="photos-tool Self-Signed"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CNF="$REPO/packaging/codesign-cert.cnf"
LOGIN_KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

# No -v: a self-signed identity is "not trusted" so `find-identity -v` hides it, but codesign
# uses it fine. Detect it policy-agnostically so we don't recreate an existing one.
if security find-identity -p codesigning 2>/dev/null | grep -qF "$CN"; then
  echo "Identity '$CN' already exists — nothing to do."
  exit 0
fi

# Homebrew OpenSSL 3 supports `pkcs12 -export -legacy`, which `security import` needs;
# /usr/bin/openssl is LibreSSL and does NOT. Pin Homebrew's, fall back to PATH.
OPENSSL="$(brew --prefix openssl@3 2>/dev/null)/bin/openssl"
[ -x "$OPENSSL" ] || OPENSSL="$(command -v openssl)"
echo "Using openssl: $OPENSSL"

KEY="$(mktemp -t pt-codesign-key)"
CRT="$(mktemp -t pt-codesign-crt)"
P12="$(mktemp -t pt-codesign-p12)"
P12_PASS="photos-tool"  # transient; only guards the .p12 we delete below
trap 'rm -f "$KEY" "$CRT" "$P12"' EXIT

"$OPENSSL" req -x509 -newkey rsa:2048 -nodes -keyout "$KEY" -out "$CRT" -days 3650 -config "$CNF"
"$OPENSSL" pkcs12 -export -legacy -inkey "$KEY" -in "$CRT" -name "$CN" -out "$P12" -passout pass:"$P12_PASS"
# -f pkcs12 is REQUIRED: the temp file has no .p12 extension, so without it `security import`
# can't detect the format and fails with "SecKeychainItemImport: Unknown format in import".
security import "$P12" -f pkcs12 -k "$LOGIN_KEYCHAIN" -P "$P12_PASS" -T /usr/bin/codesign

# OPTIONAL: pre-authorize codesign to use the key so builds don't pop a keychain prompt. This
# needs your login/keychain password; if it's wrong or skipped, the cert still works fine —
# codesign just asks once on the first build, where you click "Always Allow". Non-fatal.
read -rsp "Login/keychain password to skip the per-build prompt (or press Enter to skip): " KCPASS
echo
if [ -n "$KCPASS" ]; then
  if security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KCPASS" \
       "$LOGIN_KEYCHAIN" >/dev/null 2>&1; then
    echo "  codesign authorized — no keychain prompt on build."
  else
    echo "  (couldn't pre-authorize — codesign will prompt once on the first build; click 'Always Allow'.)"
  fi
fi

# No -v: the self-signed identity is "not trusted" but codesign uses it fine.
if security find-identity -p codesigning | grep -qF "$CN"; then
  echo "Created code-signing identity '$CN'."
  echo "Now run: ./scripts/build-app.sh --install   (it will sign with this identity)."
  echo "Then run the one-time tccutil reset + re-grant noted at the top of this script."
else
  echo "ERROR: identity creation failed." >&2
  exit 1
fi
