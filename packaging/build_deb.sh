#!/usr/bin/env bash
# Build dist/pyhantek_<version>_amd64.deb
# Needs: uv, libusb-1.0-0, dpkg-deb. Installs the frozen app to /opt/pyhantek, a `pyhantek` command,
# the applications-menu entry + icon, and the udev rule for USB access.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
BUILD="$ROOT/build"

source packaging/freeze_linux.sh   # -> $BUILD/dist/pyhantek/, $VERSION

# Stage outside the checkout: dpkg-deb rejects the 777 permissions of a Windows (/mnt/c) folder under WSL
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/pyhantek"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/512x512/apps" "$PKG/usr/share/pixmaps" "$PKG/lib/udev/rules.d" \
         "$PKG/usr/share/doc/pyhantek"

cp -r "$BUILD/dist/pyhantek" "$PKG/opt/pyhantek"
chmod -R u=rwX,go=rX "$PKG/opt/pyhantek"
chmod 755 "$PKG/opt/pyhantek/pyhantek"
ln -s /opt/pyhantek/pyhantek "$PKG/usr/bin/pyhantek"
install -m 644 packaging/pyhantek.desktop "$PKG/usr/share/applications/pyhantek.desktop"
install -m 644 pyhantek/assets/pyhantek.png "$PKG/usr/share/icons/hicolor/512x512/apps/pyhantek.png"
install -m 644 pyhantek/assets/pyhantek.png "$PKG/usr/share/pixmaps/pyhantek.png"
install -m 644 pyhantek/assets/60-openhantek.rules "$PKG/lib/udev/rules.d/60-openhantek.rules"
cat > "$PKG/usr/share/doc/pyhantek/copyright" <<'EOF'
PyHantek is licensed under the GNU General Public License v3.0 or later.
On Debian systems the full text is in /usr/share/common-licenses/GPL-3.
EOF

# Reload udev so the scope works without a reboot (replugging it is still needed if it was connected).
# The menu and icon caches are refreshed by dpkg triggers.
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ] && command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
    udevadm trigger --subsystem-match=usb --action=change || true
fi
exit 0
EOF
cat > "$PKG/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ] && command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
fi
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"

GLIBC="$(ldd --version | head -1 | grep -oE '[0-9]+\.[0-9]+$')"
cat > "$PKG/DEBIAN/control" <<EOF
Package: pyhantek
Version: $VERSION
Architecture: amd64
Maintainer: Andre Carvalho <andrejrcarvalho@outlook.pt>
Installed-Size: $(du -sk "$PKG" --exclude=DEBIAN | cut -f1)
Depends: libc6 (>= $GLIBC), libgl1, libegl1, libfontconfig1, libdbus-1-3, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libxcb-xkb1
Section: electronics
Priority: optional
Homepage: https://github.com/andrejrcarvalho/PyHantek
Description: Oscilloscope app for Hantek DSO-6022 USB scopes
 Modern Python/Qt oscilloscope application for the Hantek DSO-6022BE/BL,
 DSO-6021 and compatible USB scopes, with protocol decoders, math channels,
 FFT/spectrogram views and an MCP server.
EOF

mkdir -p dist
OUT="dist/pyhantek_${VERSION}_amd64.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT"
echo "Built $OUT"
