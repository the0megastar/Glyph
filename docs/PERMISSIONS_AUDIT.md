# Glyph Flatpak Least-Privilege Permissions Audit

This document provides a technical audit of Flatpak permissions for Glyph (`io.github.the0megastar.Glyph.yaml`), explains why each permission is required, details empirical sandbox verification results, and provides clear architectural distinctions between Flatpak sandboxing and native packaging.

---

## 1. Current Manifest Permissions & Technical Rationale

| Manifest Grant | Scope | Purpose & Justification |
|---|---|---|
| `--share=ipc` | IPC namespace | Required for the **X11 MIT-SHM** shared memory extension. When running under X11 (via `--socket=fallback-x11`), this avoids socket copying overhead for image buffers. *(Note: Wayland does not use SysV IPC; it passes file descriptors via `memfd_create` over the Unix socket).* |
| `--socket=fallback-x11` | Display | Allows fallback window presentation in native X11 sessions. |
| `--socket=wayland` | Display | Native Wayland window surface for GTK 4 and Libadwaita. |
| `--device=dri` | GPU | Hardware-accelerated rendering (OpenGL/Vulkan) via Mesa drivers. |
| `--filesystem=xdg-data/applications:create` | User writes | Confined write access to `~/.local/share/applications/`. Allows creating and removing launcher overrides without granting access to the rest of `~/.local` or `$HOME`. |
| `--filesystem=host-os:ro` | Host system (read-only) | Mounts `/usr/share/applications/` and `/usr/share/icons/` from the host OS at `/run/host/usr/` to discover native packages (RPM, DEB, Arch, etc.). |
| `--filesystem=/var/lib/flatpak/exports/share:ro` | System Flatpak exports | Enumerates system-installed Flatpak application desktop files and icons. |
| `--filesystem=/var/lib/flatpak/app:ro` | System Flatpak deployments | **Mandatory for symlink resolution**. System Flatpak exported desktop files and icons are relative symlinks (`../../../app/...`) pointing directly into `/var/lib/flatpak/app/`. |
| `--filesystem=xdg-data/flatpak/exports/share:ro` | User Flatpak exports | Enumerates user-installed Flatpak desktop files and icons in `~/.local/share/flatpak/exports/share/`. |
| `--filesystem=xdg-data/flatpak/app:ro` | User Flatpak deployments | **Mandatory for symlink resolution**. User Flatpak exported files are relative symlinks pointing into `~/.local/share/flatpak/app/`. |

---

## 2. Technical Findings & Verification

### A. IPC Namespace: X11 vs. Wayland

1. **Wayland Mechanism**:
   - Wayland compositors and clients do **not** use SysV IPC or shared memory namespaces (`shmget`/`shmat`).
   - Wayland buffer sharing uses file descriptors created with `memfd_create` (or anonymous temporary files) passed over the Unix domain socket (`--socket=wayland`).
   - Omitting `--share=ipc` has **zero effect** on native Wayland sessions.
2. **X11 Mechanism (MIT-SHM)**:
   - X11's MIT-SHM extension requires shared SysV memory segments between the X server and client.
   - If `--share=ipc` is removed, GTK 4 can still connect to X11, but MIT-SHM fails and GTK must fall back to transmitting pixel data across the X11 socket via `XPutImage`, increasing CPU and latency.
3. **Conclusion**:
   - `--share=ipc` is retained strictly to maintain full advertised performance for X11 sessions (`--socket=fallback-x11`). It does not grant filesystem or network access.

### B. Flatpak Deployment Directories (`/var/lib/flatpak/app` and `xdg-data/flatpak/app`)

Exported Flatpak desktop files and application icons are not standalone copies; they are relative symlinks:
```text
/var/lib/flatpak/exports/share/applications/com.example.App.desktop ->
  ../../../app/com.example.App/current/active/export/share/applications/com.example.App.desktop

/var/lib/flatpak/exports/share/icons/hicolor/128x128/apps/com.example.App.png ->
  ../../../../../../app/com.example.App/current/active/export/share/icons/hicolor/128x128/apps/com.example.App.png
```

**Empirical Sandbox Test**:
- When `--filesystem=/var/lib/flatpak/app:ro` is provided:
  All exported desktop files and application icons resolve and read cleanly.
- When `--filesystem=/var/lib/flatpak/app:ro` is removed:
  Kernel symlink resolution fails with `ENOENT` (`head: cannot open '...': No such file or directory`).
  Flatpak application discovery and icon loading fail completely.
- **Conclusion**:
  Both `/var/lib/flatpak/app:ro` and `xdg-data/flatpak/app:ro` are strictly required to resolve relative symlinks.

### C. Confined Writes & Least Privilege

- **No Whole-Home Access**: `--filesystem=home` is omitted. Glyph only has write access to `xdg-data/applications:create`.
- **No Network Access**: `--share=network` is omitted.
- **No Unrestricted D-Bus**: Unrestricted session bus access and `org.freedesktop.Flatpak` (flatpak-spawn) are omitted.
- **No Host Execution**: Glyph cannot invoke arbitrary binaries on the host OS.

---

## 3. Flatpak Sandboxing vs. Native Packages

| Capability / Restriction | Native Packages (`.rpm`, `.deb`, AUR, AppImage) | Flatpak Package (`io.github.the0megastar.Glyph`) |
|---|---|---|
| **Execution Context** | Runs directly on host with ordinary user privileges. | Runs inside bubblewrap container with isolated namespaces. |
| **System Application Discovery** | Directly reads `/usr/share/applications` and `/usr/share/icons`. | Reads `/run/host/usr/share/applications` via `host-os:ro`. |
| **Flatpak Application Discovery** | Directly reads `/var/lib/flatpak` and `~/.local/share/flatpak`. | Reads exported shares and deployment directories via `:ro` mounts. |
| **User Launcher Customization** | Writable access to `~/.local/share/applications`. | Confined write access via `xdg-data/applications:create`. |
| **Host Application Launching** | Native launch supported (`Gio.DesktopAppInfo.launch_uris`). | **Disabled in UI**: Sandboxed environment cannot execute host binaries directly; UI displays an informative tooltip. |
| **File Import / Export** | Direct filesystem access or native GTK file dialog. | Transparently mediated via XDG Desktop Portal (`org.freedesktop.portal.FileChooser`). |
| **TryExec Handling** | Validates executable presence on host `PATH`. | **Deliberately bypassed for host launchers**: Sandbox `PATH` does not reflect host binaries. |

---

## 4. Crucial Sandbox Principles

### In-App Toggles Do Not Alter Sandbox Grants
Flatpak filesystem mounts are established by `bubblewrap` at the moment the sandbox process is spawned by the kernel.
- An in-app setting or toggle (e.g. "Disable Native Discovery") can tell Glyph to ignore `/run/host/usr`, but **it cannot revoke the filesystem mount from the Linux kernel**.
- Glyph **must not** mislead users into believing that an in-app toggle reduces the sandbox's actual system grants.
- User-level revocation can be performed using standard Flatpak tooling:
  ```bash
  flatpak override --user --nofilesystem=host-os io.github.the0megastar.Glyph
  ```
  If this override is applied, Glyph continues functioning gracefully, discovering Flatpak and Local applications without crashing.

### Flathub Review Considerations
1. `host-os:ro` triggers a warning in `flatpak-builder-lint` as a broad filesystem permission.
2. An exception request must be filed explaining:
   - Glyph is a desktop launcher manager whose core utility is customizing native installed application launchers and icons.
   - Glyph does not request network access, whole-home access, or host-execution bridges.
3. If Flathub reviewers reject the `host-os:ro` exception, self-distribution (via standalone `.flatpak` bundles, direct remotes, `.deb`, `.rpm`, `.AppImage`, and AUR) provides complete native application discovery.
