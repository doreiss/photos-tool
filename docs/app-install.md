# Installing the photos-tool menu-bar app (no Terminal for the family member)

This builds, signs, and installs the no-Terminal `.app`. The `.app` bundles its own exiftool
(run via the system perl) and self-reinvokes its own signed binary, so it needs **no Homebrew**
on the target Mac. Do this once on the Mac that will run backups.

## 1. One-time build environment

```bash
cd ~/dev/photos-tool
/opt/homebrew/opt/python@3.11/bin/python3.11 -m venv .venv-app   # a framework Python builds the most portable bundle
.venv-app/bin/python -m pip install -e '.[gui]' -r requirements-build.txt
```

## 2. Create the stable signing identity (once — so grants persist)

```bash
./packaging/create-codesign-cert.sh   # enter your login/keychain password when asked
```

This creates a self-signed `photos-tool Self-Signed` identity. Signing the app with it gives a
designated requirement of `identifier + certificate leaf` (not a per-build cdhash), so the macOS
grants you give the app **survive every future rebuild**. It does *not* make the app notarized.

(Skip this and the app still works, ad-hoc signed — but you'd re-grant after each rebuild.)

## 3. Build + install

```bash
./scripts/build-app.sh --install
```

It fetches + verifies exiftool, builds with PyInstaller, signs with the stable identity (you'll
see `designated requirement is identifier+leaf`), smoke-tests the bundle, and copies it to
`/Applications`. On the first build, click **Always Allow** if macOS asks codesign to use the key.

> **Only if you previously ran an ad-hoc-signed build of this app**, clear the old identity's
> grants once so they don't ghost (a *fresh* Mac skips this):
> ```bash
> tccutil reset SystemPolicyAllFiles com.dominicreiss.photos-tool
> tccutil reset AppleEvents          com.dominicreiss.photos-tool
> tccutil reset Photos               com.dominicreiss.photos-tool
> ```

## 4. First launch + the one-time grants (nominal onboarding)

The app is un-notarized (expected; `spctl` reports REJECTED), so the first open needs Gatekeeper's
manual approval:

1. **Open it once:** right-click `/Applications/photos-tool.app` → **Open** → **Open**.
2. **Login Items:** System Settings → General → Login Items → add photos-tool (so it starts at login).
3. **Full Disk Access** (the one grant macOS never prompts for): System Settings → Privacy &
   Security → Full Disk Access → enable photos-tool → **quit and reopen** the app.
4. **Set up connection:** click the 📷 menu → *Set up connection…* → enter `smb://<pc>/<share>`
   (macOS's own dialog handles the password into your Keychain; the tool never sees it).
5. **Automation → Photos:** the first **Send Selected Photos** pops *"photos-tool" wants to
   control "Photos"* → **Allow** (this lets it read your selection).
6. **Photos:** the first **Clean up last backup** asks for Photos access → **Allow** (for the
   recoverable delete).

Steps 3, 5, 6 are the only permissions, granted once. Because the app is signed with the stable
identity, **they persist** — rebuilding/reinstalling never re-prompts. Run *Run Diagnostics* to
confirm every check is green.
