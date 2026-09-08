# AppImage runtime

Build natively on Ubuntu 24.04 for x86_64 or aarch64. The image includes Python,
PyGObject, GTK 4, Libadwaita, required typelibs and shared libraries, image loaders,
schemas, MIME data, and icon themes. It requires host glibc 2.39 or newer and a
working Linux graphical session with system graphics drivers. It does not require
Python or GTK packages on the target system.

In an Ubuntu 24.04 build container, install:

```sh
apt-get update
apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
  gir1.2-adw-1 gir1.2-girepository-2.0 libgdk-pixbuf2.0-bin \
  librsvg2-common webp-pixbuf-loader adwaita-icon-theme shared-mime-info \
  dconf-gsettings-backend patchelf binutils file wget ca-certificates
bash scripts/build_appimage.sh
```

Choose a fresh `APPDIR` for each build; existing directories are never deleted.
`NO_APPIMAGETOOL=1` stages only the runtime for development. `ARCH` must match the
build machine. CI pins both architectures to Ubuntu 24.04.

From the repository root, validate the resulting artifact with Docker or Podman:

```sh
CONTAINER_ENGINE=podman bash scripts/test_appimage.sh Glyph-0.1.3-x86_64.AppImage
```

This gate extracts the actual artifact without FUSE, relocates it to a path with
spaces, and starts it under Xvfb in a container without Python, GTK, or Libadwaita.
It checks the window, preferences, About dialog, schemas, and image loaders.
Both CI and release publishing require this gate to pass.
