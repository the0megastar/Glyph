# Glyph Flatpak Packaging & Flathub Guidelines

This directory contains packaging metadata and linter exception configurations for Glyph (`io.github.the0megastar.Glyph`).

## Linter Exceptions (`exceptions.json`)

When linting with `flatpak-builder-lint`, the following permission exceptions are declared for Glyph:

1. **`finish-args-unnecessary-xdg-data-applications-create-access`**:
   - **Permission**: `--filesystem=xdg-data/applications:create`
   - **Rationale**: The core utility of Glyph is creating and managing custom application launchers (`.desktop` files) and icon overrides at the user level in `~/.local/share/applications/`.
2. **`finish-args-host-os-ro-filesystem-access`**:
   - **Permission**: `--filesystem=host-os:ro`
   - **Rationale**: Glyph is a desktop application manager whose core feature is customizing application launcher names and icons. Read-only access to `/run/host/usr/share/applications` and `/run/host/usr/share/icons` is necessary to discover native distribution packages (e.g. RPM, DEB, Arch).
3. **`finish-args-flatpak-system-folder-exports-share-ro-access`** & **`finish-args-flatpak-system-folder-exports-share-applications-ro-access`**:
   - **Permission**: `--filesystem=/var/lib/flatpak/exports/share:ro`
   - **Rationale**: Required to discover system-installed Flatpak applications and their launcher metadata.
4. **`finish-args-flatpak-system-folder-app-ro-access`**:
   - **Permission**: `--filesystem=/var/lib/flatpak/app:ro`
   - **Rationale**: System Flatpak exported desktop files and icons are relative symlinks (`../../../app/...`) pointing directly into `/var/lib/flatpak/app/`. This read-only mount is required for the Linux kernel to resolve symlinks.
5. **`finish-args-unnecessary-xdg-data-flatpak-ro-access`**:
   - **Permission**: `--filesystem=xdg-data/flatpak/exports/share:ro` and `xdg-data/flatpak/app:ro`
   - **Rationale**: Required to discover and resolve user-installed Flatpak applications and symlinked assets.

For a detailed technical audit of these permissions and sandbox boundaries, see [`docs/PERMISSIONS_AUDIT.md`](../../docs/PERMISSIONS_AUDIT.md) if retained locally.
