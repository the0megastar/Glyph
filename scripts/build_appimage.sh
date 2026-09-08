#!/usr/bin/env bash
# Standalone AppImage bundler for Glyph (x86_64 and aarch64)
# Packages Python, PyGObject, GTK4, Libadwaita, typelibs, schemas, and loaders.
set -euo pipefail

# A fixed build distribution gives both architectures the same glibc baseline.
build_os=$(. /etc/os-release; printf '%s:%s' "$ID" "$VERSION_ID")
if [[ "$build_os" != ubuntu:24.04 ]]; then
  echo "Build the AppImage inside Ubuntu 24.04 (see packaging/appimage/README.md)." >&2
  exit 1
fi
ARCH="${ARCH:-$(uname -m)}"
[[ "$ARCH" == "$(uname -m)" ]] || { echo "Cross-compilation is not supported." >&2; exit 1; }
VERSION="${VERSION:-0.1.3}"
APPDIR="${APPDIR:-$(pwd)/_AppDir_${ARCH}}"
OUTPUT="${OUTPUT:-Glyph-${VERSION}-${ARCH}.AppImage}"

echo "=== Building Glyph AppImage ==="
echo "Architecture: ${ARCH}"
echo "Version:      ${VERSION}"
echo "AppDir:       ${APPDIR}"
echo "Output:       ${OUTPUT}"

APPDIR=$(realpath -m "$APPDIR")
[[ ! -e "$APPDIR" ]] || { echo "AppDir already exists; choose a fresh APPDIR: $APPDIR" >&2; exit 1; }
mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/lib"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/metainfo"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/scalable/apps"
mkdir -p "${APPDIR}/usr/share/glib-2.0/schemas"
mkdir -p "${APPDIR}/usr/lib/girepository-1.0"

# 1. Install Glyph application files
echo "--> Staging Glyph source and metadata..."
mkdir -p "${APPDIR}/usr/lib/python3/dist-packages"
cp -r src/glyph "${APPDIR}/usr/lib/python3/dist-packages/"
cp src/glyph.in "${APPDIR}/usr/bin/glyph"
chmod +x "${APPDIR}/usr/bin/glyph"

cp data/io.github.the0megastar.Glyph.desktop "${APPDIR}/usr/share/applications/"
cp data/io.github.the0megastar.Glyph.desktop "${APPDIR}/"
cp data/io.github.the0megastar.Glyph.metainfo.xml "${APPDIR}/usr/share/metainfo/"
cp data/icons/io.github.the0megastar.Glyph.svg "${APPDIR}/usr/share/icons/hicolor/scalable/apps/"
cp data/icons/io.github.the0megastar.Glyph.svg "${APPDIR}/io.github.the0megastar.Glyph.svg"
if [ -f "data/icons/io.github.the0megastar.Glyph.png" ]; then
  mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
  cp data/icons/io.github.the0megastar.Glyph.png "${APPDIR}/usr/share/icons/hicolor/256x256/apps/"
  cp data/icons/io.github.the0megastar.Glyph.png "${APPDIR}/io.github.the0megastar.Glyph.png"
  cp data/icons/io.github.the0megastar.Glyph.png "${APPDIR}/.DirIcon"
else
  cp data/icons/io.github.the0megastar.Glyph.svg "${APPDIR}/.DirIcon"
fi

# Resolve the Python, introspection and image-loader runtime before packaging.
/usr/bin/python3 scripts/bundle_appimage_runtime.py "$APPDIR"
cp packaging/appimage/bootstrap.py "$APPDIR/usr/bin/glyph-bootstrap.py"
cp scripts/smoke_appimage.py "$APPDIR/usr/bin/glyph-smoke.py"
cp packaging/appimage/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

# Package without FUSE; keep downloaded tooling and its extraction isolated.
if [ "${NO_APPIMAGETOOL:-0}" = "1" ]; then
  echo "--> Staged AppDir ready at ${APPDIR} (appimagetool skipped by NO_APPIMAGETOOL=1)"
  exit 0
fi

tool_dir=$(mktemp -d)
trap 'rm -rf -- "$tool_dir"' EXIT
if command -v appimagetool >/dev/null; then
  tool=$(command -v appimagetool)
else
  url="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
  wget -q "$url" -O "$tool_dir/appimagetool"
  chmod +x "$tool_dir/appimagetool"
  (cd "$tool_dir" && ./appimagetool --appimage-extract >/dev/null)
  tool="$tool_dir/squashfs-root/AppRun"
fi
ARCH="$ARCH" "$tool" "$APPDIR" "$OUTPUT"
test -s "$OUTPUT"
echo "=== AppImage bundle complete: ${OUTPUT} ==="
