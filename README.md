# Glyph

A modern Linux desktop application to browse installed applications, customize launcher icons and display names, and easily restore originals.

<p align="center">
  <img src="data/screenshots/library.png" alt="Glyph Library View" width="420" />
  &nbsp;
  <img src="data/screenshots/detail.png" alt="Glyph Detail View" width="420" />
</p>

Glyph never edits system files under `/usr/share/applications` or Flatpak/Snap export directories. It safely creates user-level overrides in `~/.local/share/applications/` (the standard FreeDesktop.org specification), copies customized icons into `~/.local/share/glyph/icons/`, and tracks state to allow clean, non-destructive reversion at any time.

## Compatibility & Desktop Environments

Glyph is built using GTK4 and Libadwaita, and follows FreeDesktop.org (XDG) standards. It runs across all major desktop environments and Linux distributions:
- **Desktop Environments**: GNOME, KDE Plasma, COSMIC, Cinnamon, XFCE, MATE, and Wayland/X11 window managers.
- **Distributions**: Fedora, Ubuntu, Debian, Arch Linux, openSUSE, SteamOS, and any system supporting Flatpak or GTK4.

## Native Requirements

- `gtk4` (>= 4.10), `libadwaita` (>= 1.4), `python3-gobject`, `desktop-file-utils`

### Fedora
```bash
sudo dnf install gtk4 libadwaita python3-gobject desktop-file-utils meson ninja-build
```

### Ubuntu / Debian
```bash
sudo apt install libgtk-4-dev libadwaita-1-dev python3-gi desktop-file-utils meson ninja-build
```

### Arch Linux
```bash
sudo pacman -S gtk4 libadwaita python-gobject desktop-file-utils meson ninja
```

## Run without installing

```bash
chmod +x run.sh
./run.sh
```

## Install for the app grid (user prefix)

```bash
meson setup build --prefix="$HOME/.local"
meson compile -C build
meson install -C build
```

That puts `glyph` on your `PATH` (if `~/.local/bin` is on it), installs `dev.the0megastar.Glyph.desktop`, and installs the app icon. You may need to log out once, or run:

```bash
update-desktop-database ~/.local/share/applications
gtk-update-icon-cache ~/.local/share/icons/hicolor
```

## Features

- **App-by-App Icon Customization**: Click "Change icon" or drag and drop any image (`.png`, `.svg`, `.webp`, `.jpg`) directly onto the 96px icon preview.
- **Custom Application Display Names**: Rename any application launcher right from the app page with automatic locale stripping and one-click undo (↺).
- **Customized Filter**: Dropdown menu on the header bar to quickly filter between all installed applications and only those with active customizations.
- **Restore Stock & Revert**:
  - Revert custom icons or reset custom display names independently.
  - For applications with local overrides shadowing system or Flatpak packages, click "Restore system default launcher" to safely remove the local override and restore upstream defaults.
- **App Details & Quick Launch**: Inspect the resolved **App folder**, **Desktop file**, and start **Command** with buttons to open locations in Files, plus a header **Launch** button to test and preview your launcher in the dash immediately.
- **Empty States**: Native `Adw.StatusPage` illustrations when searches yield no matches or when no apps have been customized.
- **Primary Menu**:
  - **Revert All Custom Icons**: Reset all Glyph overrides at once with a confirmation dialog.
  - **Restore All to System Default**: Remove all local launcher overrides shadowing system packages (including manual edits or third-party menu editors) and reset every app to its original package name and icon.
  - **Backup & Restore Overrides**: Export or import your customizations as a `.tar.gz` bundle for backups or dotfile synchronization across machines.
  - **Open Data Folder in Files**: Inspect `~/.local/share/glyph/` directly.
  - **Keyboard Shortcuts**: View built-in shortcuts (`Ctrl+F`, `Ctrl+Q`, `Ctrl+?`, `Esc`).

## Sources

- **System**: Native applications installed system-wide via distro package managers (RPM, DEB, Pacman, etc.) under `/usr/share/applications/`.
- **Flatpak**: Sandboxed Flatpak applications from system or user installations.
- **Snap**: Applications installed via Snap.
- **Local**: Applications installed directly in user space (e.g. `~/.local/bin/` or custom AppImage launchers).

## How revert works

- If Glyph created the local `.desktop` file, revert **deletes** it so the system or Flatpak entry is used again.
- If you already had a local `.desktop` file, revert restores only the previous `Icon=` value.
- "Restore stock" removes the local `.desktop` override completely so the desktop falls back to the original package.
- Copied images and `~/.local/share/glyph/overrides.json` are cleaned up.

## Icon Cache & Desktop Refresh

- **KDE Plasma, XFCE, COSMIC, Cinnamon**: Automatically refresh application menus immediately via inotify.
- **GNOME Shell**: GNOME Shell caches the app grid aggressively. Launching the customized app updates the dash/dock icon immediately, but the full-screen App Grid may take a session logout and login to reload its image cache.
- Launcher icons are what this app changes. The icon inside a running window’s titlebar is provided by the application process itself and is out of scope.


## Support & Sponsorship

If you find Glyph useful and would like to support its continued development, consider buying a coffee!

[![Support on Ko-fi](https://img.shields.io/badge/Ko--fi-F16061?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/the0megastar)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-EA4AAA?style=for-the-badge&logo=github-sponsors&logoColor=white)](https://github.com/sponsors/the0megastar)

## Development

```bash
meson setup build
meson compile -C build
meson devenv -C build python3 -m glyph
```

## Flatpak

Glyph can be built and run as a sandboxed Flatpak using the included manifest (`dev.the0megastar.Glyph.yaml`).

### Building with Flatpak Builder

```bash
flatpak-builder --user --install --force-clean build-flatpak dev.the0megastar.Glyph.yaml
flatpak run dev.the0megastar.Glyph
```
