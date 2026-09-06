# Glyph Flatpak Least-Privilege Permissions Audit

This document provides a technical audit of Flatpak permissions for Glyph (`io.github.the0megastar.Glyph.yaml`), explains why each permission is currently required, evaluates sandbox limitations, and documents the Flathub review considerations.

---

## 1. Current Manifest Permissions & Technical Rationale

| Manifest Grant | Scope | Purpose & Justification |
|---|---|---|
| `--share=ipc` | IPC namespace | Shared memory for Wayland and X11 rendering performance. |
| `--socket=fallback-x11` | Display | GUI fallback for X11 sessions. |
| `--socket=wayland` | Display | Native Wayland window surface for GTK4 and Libadwaita. |
| `--device=dri` | GPU | Hardware-accelerated OpenGL/Vulkan rendering. |
| `--filesystem=xdg-data/applications:create` | User writes | Confined write access to `~/.local/share/applications/`. Allows creating and removing launcher overrides without granting access to the rest of `~/.local` or `$HOME`. |
| `--filesystem=host-os:ro` | Host system (read-only) | Reads `/usr/share/applications/` and `/usr/share/icons/` on the host OS to discover native applications (RPM, DEB, Arch, etc.). |
| `--filesystem=/var/lib/flatpak/exports/share:ro` | System Flatpak exports | Enumerates system-installed Flatpak application desktop files. |
| `--filesystem=/var/lib/flatpak/app:ro` | System Flatpak apps | Required for symlink resolution. Exported Flatpak desktop files are symlinks pointing into `/var/lib/flatpak/app/<id>/current/active/export/...`. |
| `--filesystem=xdg-data/flatpak/exports/share:ro` | User Flatpak exports | Enumerates user-installed Flatpak desktop files in `~/.local/share/flatpak/exports/share/applications/`. |
| `--filesystem=xdg-data/flatpak/app:ro` | User Flatpak apps | Required for symlink resolution for user-installed Flatpak applications. |

---

## 2. Crucial Sandbox Principles

### In-App Toggles Do Not Alter Sandbox Grants
Flatpak filesystem mounts are established by `bubblewrap` at the moment the sandbox process starts.
- An in-app toggle (e.g. "Disable Native Discovery") can tell Glyph to stop reading from `/run/host/usr`, but **it cannot revoke the filesystem mount from the Linux kernel**.
- Glyph **must not** pretend to the user that an in-app toggle reduces the sandbox's actual system grants.
- User overrides can be managed externally by users via `flatpak override --user --nofilesystem=host-os io.github.the0megastar.Glyph`. If a user applies this override, Glyph gracefully continues functioning by enumerating Flatpak and Local applications without crashing.

### Symlink Resolution Requirement
On Flatpak installations, exported desktop files under `/var/lib/flatpak/exports/share/applications/` are relative symlinks:
```text
/var/lib/flatpak/exports/share/applications/org.example.App.desktop ->
  ../../../app/org.example.App/current/active/export/share/applications/org.example.App.desktop
```
If `--filesystem=/var/lib/flatpak/app:ro` is omitted, reading the symlink target fails with `ENOENT`, breaking Flatpak application discovery and icon resolution.

### Reserved Paths in Flatpak
Flatpak forbids granting arbitrary filesystem subpaths under `/usr` or `/run/host`:
- `--filesystem=/usr/share/applications:ro` is rejected by flatpak-builder because `/usr` is reserved for the Flatpak runtime.
- `--filesystem=/run/host/...` is reserved and cannot be mounted directly via `--filesystem`.
- Therefore, `host-os:ro` is currently the only standard mechanism to mount host `/usr/share/applications` (mounted inside the sandbox at `/run/host/usr/share/applications`).

---

## 3. Flathub Submission & Linter Considerations

Flathub policy discourages broad filesystem access:
1. `host-os:ro` triggers a warning/error in the Flathub builder linter (`flatpak-builder-lint`) as an overreaching filesystem permission.
2. An exception request must be filed explaining that:
   - Glyph is a desktop launcher manager whose core utility is customizing native installed application launchers and icons.
   - Glyph does not request network access (`--share=network` is omitted).
   - Glyph does not request whole-home access (`--filesystem=home` is omitted).
   - Glyph does not request write access to system directories (only `xdg-data/applications:create`).
   - Glyph does not request `flatpak-spawn` or host execution bridges.
3. If Flathub reviewers reject the `host-os:ro` exception, self-distribution (via standalone `.flatpak` bundles, direct Flatpak remotes, `.deb`, `.rpm`, `.AppImage`, and AUR) provides full native application discovery without restricting application capabilities.
