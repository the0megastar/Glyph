# Glyph Testing Guide

This guide details how to run, write, and maintain test suites for Glyph.

## Test Suite Overview

Glyph contains automated regression and unit tests organized in the `tests/` directory:

| Test File | Focus Area | Test Count |
|---|---|---|
| [`tests/test_paths.py`](file:///home/the0megastar/Glyph/tests/test_paths.py) | XDG Base Directory specification compliance, stock directory discovery, desktop ID indexing (flat vs nested), empty/custom `XDG_DATA_DIRS`. | 10 tests |
| [`tests/test_overrides.py`](file:///home/the0megastar/Glyph/tests/test_overrides.py) | Transaction logging, rollback, single and bulk reverts (icons & names), legacy v0.1.2 migration, backup v2 export/import, traversal rejection. | 19 tests |
| [`tests/test_catalog.py`](file:///home/the0megastar/Glyph/tests/test_catalog.py) | Application catalog enumeration, single stock index scan per reload, source classification (System, Flatpak, Snap, Local). | 3 tests |
| [`tests/test_ui.py`](file:///home/the0megastar/Glyph/tests/test_ui.py) | Headless GTK4/Libadwaita UI error boundaries, damaged state handling in `reload()`, Flatpak launch capability restrictions, primary menu actions. | 3 tests |
| **Total** | | **35 tests** |

---

## Running Tests Locally

### Running All Tests via Python `unittest`

From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m unittest discover -s tests -v
```

### Running Individual Test Files

Each test file includes a standalone path bootstrap and `__main__` block, allowing direct execution:

```bash
# Path and XDG tests
python3 tests/test_paths.py

# Override and transaction tests
python3 tests/test_overrides.py

# Catalog indexing tests
python3 tests/test_catalog.py

# UI error boundary tests
python3 tests/test_ui.py
```

### Running with Meson

If `meson` is installed on your host system:

```bash
meson setup _build
meson test -C _build -v
```

---

## Review Regression Checks

To verify all 10 defect fixes from the second code review (`glyph-v0.1.3-code-review.md`):

```bash
python3 /path/to/glyph-v0.1.3-review-checks.py
```

All 10 checks verify temporary fixtures without modifying the active user environment or repository.

---

## Packaging Smoke Tests in Containers

### 1. Fedora RPM Smoke Test

Run a clean container to build and install the RPM outside of git:

```bash
podman run --rm -it -v "$PWD:/workspace:z" -w /workspace fedora:latest bash -c "
  dnf install -y meson ninja-build rpm-build python3-devel python3-gobject-devel gtk4-devel libadwaita-devel desktop-file-utils
  mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
  tar --exclude='.git' -czf ~/rpmbuild/SOURCES/glyph-0.1.3.tar.gz -C .. $(basename \$PWD)
  cp packaging/rpm/glyph.spec ~/rpmbuild/SPECS/
  rpmbuild -ba ~/rpmbuild/SPECS/glyph.spec
  dnf install -y ~/rpmbuild/RPMS/noarch/glyph-0.1.3-*.rpm
  cd /tmp
  python3 -c 'import glyph.main; print(\"RPM site-packages import OK\")'
"
```

### 2. Arch Linux PKGBUILD Test

```bash
podman run --rm -it -v "$PWD:/workspace:z" -w /workspace archlinux:latest bash -c "
  pacman -Syu --noconfirm base-devel meson ninja python python-gobject gtk4 libadwaita desktop-file-utils git
  meson setup _build_arch --prefix=/usr
  meson compile -C _build_arch
  meson test -C _build_arch --print-errorlogs
"
```

### 3. AppImage Extraction & Runtime Test

```bash
NO_APPIMAGETOOL=1 ./scripts/build_appimage.sh
test -f _AppDir_x86_64/AppRun
test -d _AppDir_x86_64/usr/lib/girepository-1.0
```

---

## Release Consistency Validation

Before tagging or releasing:

```bash
python3 scripts/validate_release.py --tag v0.1.3
```

This verifies:
- `src/glyph/__init__.py`
- `meson.build`
- `data/io.github.the0megastar.Glyph.metainfo.xml`
- `packaging/rpm/glyph.spec`
- `packaging/aur/PKGBUILD`
- `desktop-file-validate` on the desktop file
- `appstreamcli validate` on the AppStream metadata

---

## How to Add New Tests

1. Create or open the relevant test file in `tests/`.
2. Inherit from `unittest.TestCase`.
3. Confine all filesystem operations to a `tempfile.TemporaryDirectory()`.
4. Always patch desktop database refreshers (`ov.refresh_desktop_database`) to avoid triggering host desktop rebuilds during test runs.
5. If testing UI logic, stub or mock GTK widgets to ensure tests run headless in CI without an active display server or X11/Wayland compositor.
