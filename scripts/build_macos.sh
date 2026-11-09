#!/usr/bin/env bash
# Builds Melophile.app and a disk image for macOS 14 or later.
#
# The app carries its own Python, every library, ffmpeg (inside imageio-ffmpeg) and Chromaprint's fpcalc,
# so it needs nothing installed. Building needs internet once; uv is fetched with Astral's installer if
# it isn't already available. No Homebrew.
#
#   scripts/build_macos.sh                       for this Mac's kind (Apple silicon or Intel)
#   MELOPHILE_ARCH=x86_64 scripts/build_macos.sh  an Intel build on an Apple silicon Mac (needs Rosetta)
#
# Output: dist/<arch>/Melophile.app and dist/Melophile-<version>-<arch>.dmg
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
HOST_ARCH="$(uname -m)"
ARCH="${MELOPHILE_ARCH:-$HOST_ARCH}"
FPCALC_VERSION=1.6.2
case "$ARCH" in
  arm64)  PY_ARCH=aarch64; FPCALC_SHA=f3bc56cb42d89a01237bb3f3629329e4ec75f7c1b0217acf6e9bf952ac47979b ;;
  x86_64) PY_ARCH=x86_64;  FPCALC_SHA=5f7b21964163e9e1719ca76d89e71873e2bdba87e9c01d1bcb06da67132da2bb ;;
  *) echo "MELOPHILE_ARCH must be arm64 or x86_64, not $ARCH" >&2; exit 1 ;;
esac
if [ "$ARCH" != "$HOST_ARCH" ]; then
  [ "$HOST_ARCH" = arm64 ] && [ "$ARCH" = x86_64 ] || { echo "An $ARCH build needs an $ARCH Mac" >&2; exit 1; }
  arch -x86_64 /usr/bin/true 2>/dev/null || { echo "Install Rosetta first: softwareupdate --install-rosetta" >&2; exit 1; }
fi
DIST="$HERE/dist"
APP="$DIST/$ARCH/Melophile.app"
RES="$APP/Contents/Resources"
TMP="$DIST/tmp-$ARCH"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"

if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
elif [ -x "$HERE/.tools/bin/uv" ]; then UV="$HERE/.tools/bin/uv"
else
  curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL="$HERE/.tools/bin" sh
  UV="$HERE/.tools/bin/uv"
fi
unset VIRTUAL_ENV UV_NATIVE_TLS UV_SYSTEM_CERTS
export UV_NO_ENV_FILE=1
# Trust the system's certificates, so downloads work behind HTTPS inspection (newer uv: UV_SYSTEM_CERTS)
case "$("$UV" help 2>/dev/null || true)" in
  *--system-certs*) export UV_SYSTEM_CERTS=1 ;;
  *) export UV_NATIVE_TLS=1 ;;
esac

echo "==> Clean"
rm -rf "$APP" "$TMP"
mkdir -p "$APP/Contents/MacOS" "$RES/bin" "$TMP"

echo "==> Python 3.12 ($ARCH)"
UV_PYTHON_INSTALL_DIR="$TMP/py" "$UV" python install "cpython-3.12-macos-$PY_ARCH-none"
# uv adds a link named for the minor version; copy the real folder behind it, not the link
SRC_PY="$(cd "$(ls -d "$TMP"/py/cpython-3.12*-macos-"$PY_ARCH"-* | head -1)" && pwd -P)"
cp -R "$SRC_PY" "$RES/python"
PY="$RES/python/bin/python3"
"$PY" -c "import platform, sys; assert platform.machine() == '$ARCH', platform.machine(); print(sys.version)"

echo "==> Melophile and its libraries"
"$UV" pip install --python "$PY" --break-system-packages --compile-bytecode "$HERE"
SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
# imageio-ffmpeg's wheel matches the build machine; make sure the ffmpeg inside is for $ARCH
FFMPEG="$("$PY" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
file "$FFMPEG" | grep -q "$ARCH" || { echo "ffmpeg isn't $ARCH: $(file "$FFMPEG")" >&2; exit 1; }

echo "==> Slimming Qt to what the app uses"
"$PY" -I "$HERE/scripts/slim_qt.py" "$SITE"

echo "==> Chromaprint fpcalc $FPCALC_VERSION (checked against its published SHA-256)"
TGZ="$TMP/fpcalc.tar.gz"
curl -LsSf -o "$TGZ" \
  "https://github.com/acoustid/chromaprint/releases/download/v$FPCALC_VERSION/chromaprint-fpcalc-$FPCALC_VERSION-macos-$ARCH.tar.gz"
echo "$FPCALC_SHA  $TGZ" | shasum -a 256 -c -
mkdir -p "$TMP/fpcalc"
tar -xzf "$TGZ" -C "$TMP/fpcalc"
cp "$(find "$TMP/fpcalc" -type f -name fpcalc | head -1)" "$RES/bin/fpcalc"
chmod 755 "$RES/bin/fpcalc"
xattr -c "$RES/bin/fpcalc" 2>/dev/null || true
cp "$HERE/THIRD_PARTY.md" "$HERE/LICENSE" "$RES/"

echo "==> Icon"
SET="$TMP/Melophile.iconset"
mkdir -p "$SET"
for s in 16 32 128 256 512; do
  sips -z $s $s src/melophile/assets/icon.png --out "$SET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) src/melophile/assets/icon.png --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$SET" -o "$RES/Melophile.icns"

echo "==> Launcher and Info.plist"
cat > "$APP/Contents/MacOS/Melophile" <<'LAUNCH'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/.." && pwd)"
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP VIRTUAL_ENV
export MELOPHILE_PACKAGED=1 MELOPHILE_BIN="$DIR/Resources/bin" PYTHONDONTWRITEBYTECODE=1
exec "$DIR/Resources/python/bin/python3" -I -m melophile "$@"
LAUNCH
chmod 755 "$APP/Contents/MacOS/Melophile"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Melophile</string>
  <key>CFBundleDisplayName</key><string>Melophile</string>
  <key>CFBundleIdentifier</key><string>io.github.dontpanicmorty.melophile</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleExecutable</key><string>Melophile</string>
  <key>CFBundleIconFile</key><string>Melophile</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.music</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppleEventsUsageDescription</key><string>Melophile reads and tidies song details in your Music library when you choose “The Music app” in Tag Cleanup.</string>
</dict></plist>
PLIST

echo "==> Checking the app is self-contained"
OUTSIDE="$(find "$APP" -type l -exec sh -c 'case "$(readlink "$1")" in /*) echo "$1";; esac' _ {} \;)"
[ -z "$OUTSIDE" ] || { echo "links pointing outside the app: $OUTSIDE" >&2; exit 1; }
rm -rf "$TMP/py"  # the self-test must run on the app's own copy of Python

echo "==> Self-test"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/Melophile" selftest

echo "==> Ad-hoc signing"
find "$APP" -name '__pycache__' -prune -o -name '.DS_Store' -delete 2>/dev/null || true
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/Melophile" selftest >/dev/null
codesign --verify --deep --strict "$APP" && echo "signature intact after a run"

echo "==> Disk image"
DMG="$DIST/Melophile-$VERSION-$ARCH.dmg"
STAGE="$TMP/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
for attempt in 1 2 3 4 5; do  # hdiutil sometimes reports "Resource busy"; it passes on a retry
  hdiutil create -volname Melophile -srcfolder "$STAGE" -ov -format UDZO "$DMG" && break
  [ "$attempt" = 5 ] && { echo "hdiutil failed 5 times" >&2; exit 1; }
  sleep 10
done
rm -rf "$TMP"
du -sh "$APP" "$DMG"
