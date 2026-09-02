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

## How revert works

- If Glyph created the local `.desktop` file, revert **deletes** it so the system or Flatpak entry is used again.
- If you already had a local `.desktop` file, revert restores only the previous `Icon=` value.
- Copied images and `~/.local/share/glyph/overrides.json` are cleaned up for that app.

## After changing an icon

Glyph writes the launcher override immediately. **GNOME Shell often keeps the old image in the app grid** until you log out and log back in. Opening the app can show the new icon on the dash for that window. Unpin/pin and `xdg-desktop-menu forceupdate` do not reliably clear the grid cache.

Glyph does not restart GNOME Shell. On Wayland there is no safe way to force a grid refresh without ending the session.

Each app page also shows the **Desktop file** path, resolved **App folder** (e.g. Flatpak active deploy directory or binary parent), and start **Command**. The folder buttons open the `.desktop` file or the installation directory in Files (Nautilus).

Launcher icons are what this app changes. The icon inside a running window’s titlebar comes from the application itself and is out of scope.

## Development

```bash
meson setup build
meson compile -C build
meson devenv -C build python3 -m glyph
```
