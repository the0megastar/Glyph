#!/usr/bin/env bash
# Verify the actual release artifact, extracted without FUSE at a different path.
set -euo pipefail
engine=${CONTAINER_ENGINE:-docker}
artifact=$(realpath "${1:?Usage: test_appimage.sh ARTIFACT.AppImage}")
"$engine" build -t glyph-appimage-smoke -f packaging/appimage/Dockerfile.smoke packaging/appimage
"$engine" run --rm --network=none --security-opt label=disable \
  -v "$artifact:/artifact.AppImage:ro" glyph-appimage-smoke '
    if command -v python3 || /sbin/ldconfig -p | grep -Eq "libgtk-4|libadwaita-1|libgirepository"; then
      echo "Smoke container unexpectedly contains application runtime dependencies" >&2
      exit 1
    fi
    mkdir -p "$HOME"
    cd /tmp
    /artifact.AppImage --appimage-extract >/dev/null
    mv squashfs-root "relocated Glyph"
    chmod -R a-w "relocated Glyph"
    timeout 30s xvfb-run -a dbus-run-session -- "./relocated Glyph/AppRun" --appimage-smoke-test
  '
