# Glyph v0.1.3 Release Notes

Glyph v0.1.3 introduces transactional safety, bulk display name restoration, portable v2 backups, nested desktop ID compliance, and multi-distribution packaging across `x86_64` and `aarch64`.

---

## What's New

### 🏷️ Bulk Name Revert ("Revert All Custom Names…")
- Added a dedicated menu action to restore original display names across all customized applications in one click.
- Completely independent from icon customization: reverting custom names retains all custom icons and pre-existing user launchers.
- Reverting custom icons retains all custom display names.

### 🛡️ Write-Ahead Transaction Journaling & Crash Recovery
- Modifications to `.desktop` files, custom icons, and `overrides.json` are recorded in an atomic write-ahead journal (`.journal.json`).
- If an operation is interrupted (e.g. power loss or process kill), the journal automatically recovers and rolls back partial writes on the next startup.
- Corrupted `overrides.json` files fail closed, preserving the damaged file and warning the user instead of allowing destructive edits.

### 📦 Portable Backups (Format Version 2)
- Backups now store relative icon assets, full custom display names, and application metadata.
- Import supports dry-run preview, conflict detection, and directory traversal rejection.
- Legacy v1 backups remain fully compatible and automatically upgrade to v2 upon import.

### 🔍 Desktop ID & Path Hardening
- Full support for nested desktop IDs (e.g. `gnome-terminal-server.desktop` under subdirectories) with deterministic flattening per FreeDesktop.org standards.
- Stock index scanning is optimized to build once per catalog reload rather than once per application.
- Compliant fallback for unset or explicitly empty `XDG_DATA_DIRS`.
- Safe cleanup of legacy v0.1.2 launcher overrides by verifying against stock source content before removal.

### 🔒 Flatpak Sandbox Launch Capability Guard
- Enforces clear sandbox boundaries: external and host applications that cannot be executed from within the Flatpak sandbox have their launch button disabled with an informative explanation tooltip, preventing silent failures.

---

## 📥 Downloads & Packages

| Package Type | Architecture | File |
|---|---|---|
| **Flatpak Bundle** | `x86_64` | `Glyph-0.1.3-x86_64.flatpak` |
| **Flatpak Bundle** | `aarch64` | `Glyph-0.1.3-aarch64.flatpak` |
| **AppImage** | `x86_64` | `Glyph-0.1.3-x86_64.AppImage` |
| **AppImage** | `aarch64` | `Glyph-0.1.3-aarch64.AppImage` |
| **Debian / Ubuntu** | `all` | `glyph-0.1.3.deb` |
| **Fedora / RHEL / openSUSE** | `noarch` | `glyph-0.1.3.rpm` |
| **Arch Linux (AUR)** | `any` | [`packaging/aur/PKGBUILD`](file:///home/the0megastar/Glyph/packaging/aur/PKGBUILD) |

---

## Minimum Runtime Requirements
- **Python**: `>= 3.10`
- **GTK**: `>= 4.10`
- **Libadwaita**: `>= 1.5`
- **PyGObject**: `>= 3.42`
- **Desktop Environment**: GNOME Shell, KDE Plasma, COSMIC, Cinnamon, XFCE, or MATE.

### Arch Linux download

The release workflow now builds a pacman package from the checked-out source using the maintained PKGBUILD and requires a fresh Arch installation check before publication. The package uses `any` architecture metadata; the installation check targets Arch Linux x86_64.

After downloading the package, install it and its declared dependencies with:

```bash
sudo pacman -U ./glyph-0.1.3-1-any.pkg.tar.zst
```

This is a direct GitHub release download, not an AUR publication or an update repository. Download subsequent packages manually to update.
