#!/usr/bin/env bash
# Install the newest Simplified Chinese Koe build from this fork's releases.
#
#   curl -fsSL https://raw.githubusercontent.com/<owner>/koe/zh-tooling/tools/zh/install.sh | bash
#
# Downloads the DMG, mounts it, and copies Koe.app into /Applications.
#
# On signing: these builds are ad-hoc signed locally. No Apple Developer ID
# certificate and no notarization are involved, so nothing here needs a
# certificate — but macOS still quarantines anything downloaded from the
# internet, so the first launch needs that flag cleared. The script asks before
# doing it, because clearing a security flag is your decision, not a silent one.
set -euo pipefail

REPO="${KOE_ZH_REPO:-}"
ASSET="Koe-zh-macOS-arm64.dmg"
DEST="/Applications/Koe.app"
TMP="$(mktemp -d)"
MOUNT=""

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

cleanup() {
  [ -n "$MOUNT" ] && hdiutil detach "$MOUNT" -quiet 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

command -v gh >/dev/null || die "gh CLI is required (brew install gh)"
command -v hdiutil >/dev/null || die "hdiutil is required (comes with macOS)"

# Default to the authenticated user's own fork, so nothing is hardcoded and the
# script works for anyone who forked this. Override with KOE_ZH_REPO=owner/repo.
if [[ -z "$REPO" ]]; then
  ME="$(gh api user --jq .login 2>/dev/null || true)"
  [[ -n "$ME" ]] || die "could not determine the GitHub account; set KOE_ZH_REPO=owner/repo"
  REPO="$ME/koe"
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  die "these builds are arm64 only (this machine is $(uname -m))"
fi

say "==> Finding the newest 中文版 release in $REPO"
TAG="$(gh release list --repo "$REPO" --limit 50 --json tagName \
       --jq '[.[] | select(.tagName | startswith("zh-v"))] | .[0].tagName')"
[[ -n "$TAG" && "$TAG" != "null" ]] || die "no zh-v* release found in $REPO"
say "    $TAG"

say "==> Downloading $ASSET"
gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --dir "$TMP" --clobber

say "==> Verifying the DMG"
hdiutil verify "$TMP/$ASSET" >/dev/null || die "the DMG failed its checksum check"

# -quiet suppresses the device table the plain-text output would carry, so the
# mount point has to be read from the plist form instead.
ATTACH_PLIST="$TMP/attach.plist"
hdiutil attach "$TMP/$ASSET" -nobrowse -readonly -plist > "$ATTACH_PLIST"
MOUNT="$(python3 - "$ATTACH_PLIST" <<'PY'
import plistlib, sys
with open(sys.argv[1], "rb") as fh:
    data = plistlib.load(fh)
for entity in data.get("system-entities", []):
    if entity.get("mount-point"):
        print(entity["mount-point"])
        break
PY
)"
[[ -d "$MOUNT" ]] || die "could not mount $TMP/$ASSET"

APP="$MOUNT/Koe.app"
[[ -d "$APP" ]] || die "Koe.app is missing from the image"
[[ -f "$APP/Contents/Resources/zh-Hans.lproj/Localizable.strings" ]] \
  || die "this build has no Chinese catalogue — wrong asset?"
codesign --verify --deep --strict "$APP" 2>/dev/null \
  || die "code signature check failed"

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
ARCHS="$(lipo -archs "$APP/Contents/MacOS/Koe")"
KEYS="$(plutil -convert json -o - "$APP/Contents/Resources/zh-Hans.lproj/Localizable.strings" \
        | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
say "    Koe $VERSION, $ARCHS, $KEYS Chinese strings, signature ok"

if /usr/libexec/PlistBuddy -c 'Print :SUFeedURL' "$APP/Contents/Info.plist" >/dev/null 2>&1; then
  say "    warning: this build still has a Sparkle feed URL — it may update itself"
else
  say "    Sparkle auto-update is disabled in this build"
fi

say ""
say "This build is ad-hoc signed, so macOS quarantines it after download and the"
say "first launch needs that flag cleared. That is your call to make."
read -r -p "Clear the quarantine flag and install to $DEST? [y/N] " reply
case "$reply" in
  [yY]*) ;;
  *) say "aborted; nothing installed. The app is at $APP (still mounted)"; exit 0 ;;
esac

if [[ -d "$DEST" ]]; then
  say "==> Replacing existing $DEST"
  osascript -e 'tell application "Koe" to quit' >/dev/null 2>&1 || true
  sleep 1
  rm -rf "$DEST"
  if brew list --cask 2>/dev/null | grep -qx koe; then
    say "    note: Homebrew owns /Applications/Koe.app. A later"
    say "          'brew upgrade --cask koe' will overwrite this Chinese build with"
    say "          the official English one. Consider 'brew uninstall --cask koe'"
    say "          (keeping this app) if you want the Chinese build to stay."
  fi
fi

ditto "$APP" "$DEST"
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
codesign --verify --deep --strict "$DEST" 2>/dev/null || die "post-install verification failed"

say ""
say "==> Installed $DEST (Koe $VERSION)"
say "    First launch: grant Microphone and Accessibility permissions when asked."
