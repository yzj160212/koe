#!/usr/bin/env bash
# Install the newest Simplified Chinese Koe build from this fork's releases.
#
#   curl -fsSL https://raw.githubusercontent.com/<owner>/koe/main/tools/zh/install.sh | bash
#
# The build is ad-hoc signed and not notarized, because it is a personal build
# that is not redistributed through a Developer ID. macOS will therefore refuse
# the first launch until you clear the quarantine flag; this script does that
# for you after a confirmation, since it is a meaningful security decision.
set -euo pipefail

REPO="${KOE_ZH_REPO:-}"
ASSET="Koe-zh-macOS-arm64.zip"
DEST="/Applications/Koe.app"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v gh >/dev/null || die "gh CLI is required (brew install gh)"
command -v ditto >/dev/null || die "ditto is required"

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

say "==> Verifying the archive unpacks to a valid app bundle"
ditto -x -k "$TMP/$ASSET" "$TMP/out"
APP="$TMP/out/Koe.app"
[[ -d "$APP" ]] || die "the archive did not contain Koe.app"
[[ -f "$APP/Contents/Resources/zh-Hans.lproj/Localizable.strings" ]] \
  || die "this build has no Chinese catalogue — wrong asset?"
codesign --verify --deep --strict "$APP" 2>/dev/null \
  || die "code signature check failed"

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
KEYS="$(plutil -convert json -o - "$APP/Contents/Resources/zh-Hans.lproj/Localizable.strings" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
say "    Koe $VERSION, $KEYS Chinese strings, signature ok"

if /usr/libexec/PlistBuddy -c 'Print :SUFeedURL' "$APP/Contents/Info.plist" >/dev/null 2>&1; then
  say "    warning: this build still has a Sparkle feed URL — it may update itself"
else
  say "    Sparkle auto-update is disabled in this build"
fi

say ""
say "This build is ad-hoc signed, not notarized. macOS quarantines anything"
say "downloaded from the internet, so the first launch needs the quarantine flag"
say "cleared. That is your call to make, not something to do silently."
read -r -p "Clear the quarantine flag and install to $DEST? [y/N] " reply
case "$reply" in
  [yY]*) ;;
  *) say "aborted; nothing installed. The app is at $APP"; exit 0 ;;
esac

if [[ -d "$DEST" ]]; then
  BREW_OWNED=""
  if brew list --cask 2>/dev/null | grep -qx koe; then
    BREW_OWNED="yes"
  fi
  say "==> Replacing existing $DEST"
  osascript -e 'tell application "Koe" to quit' >/dev/null 2>&1 || true
  sleep 1
  rm -rf "$DEST"
  if [[ -n "$BREW_OWNED" ]]; then
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
say "    Language: Settings ▸ the interface follows macOS, or force 简体中文 in the"
say "    language picker if this build exposes one."
