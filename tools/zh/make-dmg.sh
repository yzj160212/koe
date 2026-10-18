#!/usr/bin/env bash
# Build a distributable DMG for Apple Silicon (arm64) from a signed .app.
#
#   make-dmg.sh <app-path> <output-dmg> [volume-name]
#
# Layout is the standard drag-to-install one: the app plus an "Applications"
# symlink. Deliberately no fancy background image or icon positioning — that
# requires the Finder via AppleScript, which is not scriptable in a headless CI
# runner and would make the build flaky.
set -euo pipefail

APP="$1"
OUT="$2"
VOLNAME="${3:-Koe}"

[ -d "$APP" ] || { echo "error: no such app bundle: $APP" >&2; exit 1; }
case "$OUT" in
  *.dmg) ;;
  *) echo "error: output must end in .dmg: $OUT" >&2; exit 1 ;;
esac

# The build is arm64-only; refuse to package something that would not run on the
# target machines, because a silently-wrong architecture is worse than no build.
ARCHS="$(lipo -archs "$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")")"
echo "app architectures: $ARCHS"
case "$ARCHS" in
  *arm64*) ;;
  *) echo "error: app is not arm64 ($ARCHS); these builds target Apple Silicon" >&2; exit 1 ;;
esac

STAGE="$(mktemp -d)"
MOUNT=""
cleanup() {
  [ -n "$MOUNT" ] && hdiutil detach "$MOUNT" -quiet 2>/dev/null || true
  rm -rf "$STAGE"
}
trap cleanup EXIT

APP_NAME="$(basename "$APP")"
echo "staging $APP_NAME into $STAGE"
ditto "$APP" "$STAGE/$APP_NAME"
ln -s /Applications "$STAGE/Applications"

rm -f "$OUT"
echo "creating $OUT"
hdiutil create \
  -volname "$VOLNAME" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  -quiet \
  "$OUT"

echo "verifying image"
hdiutil verify "$OUT" >/dev/null

# Mount it and confirm the payload is really inside — a DMG that fails to open
# or that lost the app would otherwise only be discovered by the user.
# -quiet suppresses the device table that the plain-text output would carry, so
# the mount point has to be read from the plist form instead.
ATTACH_PLIST="$(mktemp)"
hdiutil attach "$OUT" -nobrowse -readonly -plist > "$ATTACH_PLIST"
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
rm -f "$ATTACH_PLIST"
[ -d "$MOUNT" ] || { echo "error: could not mount $OUT" >&2; exit 1; }
[ -d "$MOUNT/$APP_NAME" ] || { echo "error: $APP_NAME missing from the image" >&2; exit 1; }
[ -L "$MOUNT/Applications" ] || { echo "error: Applications symlink missing from the image" >&2; exit 1; }
echo "contents verified: $APP_NAME + Applications symlink"

hdiutil detach "$MOUNT" -quiet
MOUNT=""

ls -lh "$OUT"
echo "built $OUT"
