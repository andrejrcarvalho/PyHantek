#!/usr/bin/env bash
# Build dist/PyHantek-<version>-x86_64.AppImage
# Needs: uv, libusb-1.0-0, curl. The result runs on distros with glibc >= the build machine's.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
BUILD="$ROOT/build"

uv sync --group build
VERSION="$(uv run python -c 'import pyhantek; print(pyhantek.__version__)')"

# Qt's X11 backend needs libxcb-cursor0, which many desktops lack. Make sure PyInstaller finds (and bundles) it,
# fetching the .deb without root if this build machine doesn't have it installed.
if ! ldconfig -p | grep -q "libxcb-cursor.so.0"; then
    EXTRA="$BUILD/extra-libs"
    if [ ! -e "$EXTRA/usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0" ] && command -v apt-get >/dev/null; then
        mkdir -p "$EXTRA"
        (cd "$EXTRA" && apt-get download libxcb-cursor0 && dpkg-deb -x libxcb-cursor0_*.deb .)
    fi
    if [ -e "$EXTRA/usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0" ]; then
        export LD_LIBRARY_PATH="$EXTRA/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    else
        echo "WARNING: libxcb-cursor.so.0 not available; the AppImage will need it on the target system" >&2
    fi
fi

uv run pyinstaller --noconfirm --clean --distpath "$BUILD/dist" --workpath "$BUILD/work" packaging/pyhantek.spec

APPDIR="$BUILD/PyHantek.AppDir"
rm -rf "${APPDIR:?}"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/512x512/apps" \
         "$APPDIR/usr/share/metainfo"
cp -a "$BUILD/dist/pyhantek" "$APPDIR/usr/bin/pyhantek"
cp pyhantek/assets/pyhantek.png "$APPDIR/pyhantek.png"
cp pyhantek/assets/pyhantek.png "$APPDIR/usr/share/icons/hicolor/512x512/apps/pyhantek.png"
cp packaging/pyhantek.desktop "$APPDIR/pyhantek.desktop"
cp packaging/pyhantek.desktop "$APPDIR/usr/share/applications/pyhantek.desktop"

cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/pyhantek/pyhantek" "$@"
RUN
chmod +x "$APPDIR/AppRun"

TOOL="$BUILD/tools/appimagetool-x86_64.AppImage"
if [ ! -x "$TOOL" ]; then
    mkdir -p "$BUILD/tools"
    curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi

mkdir -p dist
OUT="dist/PyHantek-$VERSION-x86_64.AppImage"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$OUT"
echo "Built $OUT"
