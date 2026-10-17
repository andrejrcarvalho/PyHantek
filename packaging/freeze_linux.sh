# Sourced by build_appimage.sh and build_deb.sh (expects $ROOT and $BUILD).
# Freezes the app with PyInstaller into $BUILD/dist/pyhantek/ and sets $VERSION.
# Set PYHANTEK_REUSE_BUILD=1 to skip PyInstaller when that folder already exists (CI builds both packages from one freeze).

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
        echo "WARNING: libxcb-cursor.so.0 not available; the package will need it on the target system" >&2
    fi
fi

if [ "${PYHANTEK_REUSE_BUILD:-0}" = 1 ] && [ -x "$BUILD/dist/pyhantek/pyhantek" ]; then
    echo "Reusing $BUILD/dist/pyhantek"
else
    uv run pyinstaller --noconfirm --clean --distpath "$BUILD/dist" --workpath "$BUILD/work" packaging/pyhantek.spec
fi
