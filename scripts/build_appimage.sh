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
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
SITELIB="${APPDIR}/usr/lib/python${PYVER}/site-packages"
mkdir -p "${SITELIB}"
cp -r src/glyph "${SITELIB}/"
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

# 3. Stage GObject Introspection Typelibs
echo "--> Bundling GI typelibs..."
TYPELIB_DIRS=(
  "/usr/lib/girepository-1.0"
  "/usr/lib64/girepository-1.0"
  "/usr/lib/${ARCH}-linux-gnu/girepository-1.0"
)
for dir in "${TYPELIB_DIRS[@]}"; do
  if [ -d "${dir}" ]; then
    for lib in Gtk-4.0 Adw-1 Gio-2.0 GObject-2.0 GLib-2.0 Gdk-4.0 GdkPixbuf-2.0 Pango-1.0 cairo-1.0 GModule-2.0 HarfBuzz-0.0; do
      find "${dir}" -name "${lib}.typelib" -exec cp -t "${APPDIR}/usr/lib/girepository-1.0/" {} + 2>/dev/null || true
    done
  fi
done

# 4. Stage shared libraries for runtime dependencies (PyGObject, GTK4, Libadwaita)
echo "--> Bundling core shared libraries..."
LIB_PATTERNS=(
  "libadwaita-1.so*"
  "libgtk-4.so*"
  "libgirepository-1.0.so*"
  "libgobject-2.0.so*"
  "libglib-2.0.so*"
  "libgio-2.0.so*"
  "libgdk_pixbuf-2.0.so*"
  "libpango-1.0.so*"
  "libcairo.so*"
  "libcairo-gobject.so*"
  "libgmodule-2.0.so*"
)
SEARCH_DIRS=(
  "/usr/lib64"
  "/usr/lib/${ARCH}-linux-gnu"
  "/usr/lib"
)

for search in "${SEARCH_DIRS[@]}"; do
  if [ -d "${search}" ]; then
    for pattern in "${LIB_PATTERNS[@]}"; do
      find "${search}" -maxdepth 1 -name "${pattern}" -exec cp -d -t "${APPDIR}/usr/lib/" {} + 2>/dev/null || true
    done
  fi
done

# 5. Stage Python standard library & gi module bindings
echo "--> Bundling Python gi module..."
python3 -c "import gi; print(gi.__file__)" 2>/dev/null | while read -r gipath; do
  gidur=$(dirname "$gipath")
  cp -r "$gidur" "${SITELIB}/" 2>/dev/null || true
done

# 6. Create robust AppRun entrypoint
echo "--> Writing AppRun entrypoint..."
cat <<'EOF' > "${APPDIR}/AppRun"
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}

export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${HERE}/usr/lib64:${HERE}/usr/lib/x86_64-linux-gnu:${HERE}/usr/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="${HERE}/usr/lib/girepository-1.0:${GI_TYPELIB_PATH:-}"
export GSETTINGS_SCHEMA_DIR="${HERE}/usr/share/glib-2.0/schemas:${GSETTINGS_SCHEMA_DIR:-}"

# Discover bundled Python version
PYDIR=$(find "${HERE}/usr/lib" -maxdepth 2 -type d -name "python3.*" 2>/dev/null | head -n 1)
if [ -n "${PYDIR}" ]; then
  export PYTHONPATH="${PYDIR}/site-packages:${PYDIR}/dist-packages:${PYTHONPATH:-}"
fi

export XDG_DATA_DIRS="${HERE}/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

# Run Glyph with host or bundled python3
if [ -x "${HERE}/usr/bin/python3" ]; then
  exec "${HERE}/usr/bin/python3" "${HERE}/usr/bin/glyph" "$@"
else
  exec python3 "${HERE}/usr/bin/glyph" "$@"
fi
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
