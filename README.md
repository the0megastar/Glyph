# Glyph

A small GNOME app for Fedora that lists your installed applications, lets you pick a new launcher icon, and can restore the original.

Glyph never edits files under `/usr/share/applications` or Flatpak/Snap export directories. It copies a `.desktop` file into `~/.local/share/applications/` (the normal user override), points `Icon=` at a copy of your image under `~/.local/share/glyph/icons/`, and records enough state to revert.

## Requirements

- Fedora with GNOME
- `gtk4`, `libadwaita`, `python3-gobject`, `desktop-file-utils`

```bash
sudo dnf install gtk4 libadwaita python3-gobject desktop-file-utils meson ninja-build
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
- **Customized Filter**: Toggle between all installed applications and only those customized with Glyph.
- **Restore Stock & Revert**:
  - Revert custom icons back to their original state with one click.
  - For applications with local overrides shadowing system or Flatpak packages, click "Restore stock" to safely remove the local launcher and reset to upstream defaults.
- **App Details**: Inspect the **Desktop file** path, resolved **App folder** (e.g. Flatpak active deploy directory or binary directory), and start **Command**, with buttons to open locations in Files (Nautilus).
- **Primary Menu**:
  - **Revert All Custom Icons**: Reset all Glyph overrides at once with a confirmation dialog.
  - **Backup & Restore Overrides**: Export or import your customizations as a `.tar.gz` bundle for backups or dotfile synchronization across machines.
  - **Open Data Folder in Files**: Inspect `~/.local/share/glyph/` directly.

## Sources

- **RPM**: System-packaged applications installed via DNF/RPM.
- **Flatpak**: Flatpak applications from system or user installations.
- **Local**: Applications installed directly in user space (e.g. `~/.local/bin/` or manual user desktop entries).

## How revert works

- If Glyph created the local `.desktop` file, revert **deletes** it so the system or Flatpak entry is used again.
- If you already had a local `.desktop` file, revert restores only the previous `Icon=` value.
- "Restore stock" removes the local `.desktop` override completely so GNOME falls back to the system package.
- Copied images and `~/.local/share/glyph/overrides.json` are cleaned up.

## After changing an icon

Glyph writes the launcher override immediately. **GNOME Shell often keeps the old image in the app grid** until you log out and log back in. Opening the app can show the new icon on the dash for that window. Unpin/pin and `xdg-desktop-menu forceupdate` do not reliably clear the grid cache.

Glyph does not restart GNOME Shell. On Wayland there is no safe way to force a grid refresh without ending the session.

Launcher icons are what this app changes. The icon inside a running window’s titlebar comes from the application itself and is out of scope.


## Development

```bash
meson setup build
meson compile -C build
meson devenv -C build python3 -m glyph
```
