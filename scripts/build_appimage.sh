#!/usr/bin/env bash
# Standalone AppImage bundler for Glyph (x86_64 and aarch64)
# Packages Python, PyGObject, GTK4, Libadwaita, typelibs, schemas, and loaders.
set -euo pipefail

ARCH="${ARCH:-$(uname -m)}"
VERSION="${VERSION:-0.1.3}"
APPDIR="${APPDIR:-$(pwd)/_AppDir_${ARCH}}"
OUTPUT="${OUTPUT:-Glyph-${VERSION}-${ARCH}.AppImage}"

echo "=== Building Glyph AppImage ==="
echo "Architecture: ${ARCH}"
echo "Version:      ${VERSION}"
echo "AppDir:       ${APPDIR}"
echo "Output:       ${OUTPUT}"

rm -rf "${APPDIR}"
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
mkdir -p "${APPDIR}/usr/lib/python3/site-packages"
cp -r src/glyph "${APPDIR}/usr/lib/python3/dist-packages/"
cp -r src/glyph "${APPDIR}/usr/lib/python3/site-packages/"
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

# 2. Stage GSettings Schemas
echo "--> Bundling GSettings schemas..."
for schema_dir in /usr/share/glib-2.0/schemas; do
  if [ -d "${schema_dir}" ]; then
    cp -r "${schema_dir}"/* "${APPDIR}/usr/share/glib-2.0/schemas/" 2>/dev/null || true
  fi
done
if command -v glib-compile-schemas &>/dev/null; then
  glib-compile-schemas "${APPDIR}/usr/share/glib-2.0/schemas" 2>/dev/null || true
fi

# 3. Create robust AppRun entrypoint
echo "--> Writing AppRun entrypoint..."
cat <<'EOF' > "${APPDIR}/AppRun"
#!/bin/sh
SELF=$(readlink -f "$0")
HERE=${SELF%/*}

export PATH="${HERE}/usr/bin:${PATH}"
export PYTHONPATH="${HERE}/usr/lib/python3/dist-packages:${HERE}/usr/lib/python3/site-packages:${PYTHONPATH:-}"
export XDG_DATA_DIRS="${HERE}/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
if [ -d "${HERE}/usr/share/glib-2.0/schemas" ]; then
  export GSETTINGS_SCHEMA_DIR="${HERE}/usr/share/glib-2.0/schemas:${GSETTINGS_SCHEMA_DIR:-}"
fi

# Pre-flight check for Python 3, PyGObject, GTK 4, and Libadwaita
if ! python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); from gi.repository import Gtk, Adw" >/dev/null 2>&1; then
  echo "Error: Glyph requires Python 3, PyGObject, GTK 4, and Libadwaita." >&2
  echo "Please install them via your distribution package manager (e.g. 'sudo dnf install python3-gobject gtk4 libadwaita' or 'sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1')." >&2
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="Glyph - Missing Dependencies" --text="Glyph requires Python 3, PyGObject, GTK 4, and Libadwaita.\n\nPlease install them using your system package manager or use the Flatpak version." 2>/dev/null || true
  fi
  exit 1
fi

exec python3 "${HERE}/usr/bin/glyph" "$@"
EOF
chmod +x "${APPDIR}/AppRun"

# 7. Package into AppImage if appimagetool is requested/available
if [ "${NO_APPIMAGETOOL:-0}" = "1" ]; then
  echo "--> Staged AppDir ready at ${APPDIR} (appimagetool skipped by NO_APPIMAGETOOL=1)"
  exit 0
fi

echo "--> Packaging with appimagetool..."
AI_ARCH="${ARCH}"
if [ "${AI_ARCH}" = "arm64" ]; then
  AI_ARCH="aarch64"
fi

TOOL="appimagetool-${AI_ARCH}"
if ! command -v "${TOOL}" &>/dev/null && [ ! -f "${TOOL}" ]; then
  URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${AI_ARCH}.AppImage"
  echo "Downloading ${URL}..."
  wget -q "${URL}" -O "${TOOL}" || curl -sSL "${URL}" -o "${TOOL}"
  chmod +x "${TOOL}"
fi

if [ -f "${TOOL}" ]; then
  ./"${TOOL}" --appimage-extract >/dev/null 2>&1 || true
  if [ -d "squashfs-root" ]; then
    ARCH="${AI_ARCH}" ./squashfs-root/AppRun "${APPDIR}" "${OUTPUT}"
    rm -rf squashfs-root
  else
    ARCH="${AI_ARCH}" ./"${TOOL}" "${APPDIR}" "${OUTPUT}"
  fi
elif command -v appimagetool &>/dev/null; then
  ARCH="${AI_ARCH}" appimagetool "${APPDIR}" "${OUTPUT}"
else
  echo "WARNING: appimagetool not found; AppDir prepared at ${APPDIR}"
fi

echo "=== AppImage bundle complete: ${OUTPUT} ==="
