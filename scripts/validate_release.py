#!/usr/bin/env python3
"""Release validation script for Glyph.
Verifies version consistency across python metadata, meson.build, metainfo.xml,
packaging specifications, and optional git tag. Validates desktop and appstream files.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent


def get_init_version() -> str:
    path = ROOT / "src/glyph/__init__.py"
    text = path.read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise ValueError(f"Could not find __version__ in {path}")
    return m.group(1).strip()


def get_meson_version() -> str:
    path = ROOT / "meson.build"
    text = path.read_text(encoding="utf-8")
    m = re.search(r"project\(\s*['\"]glyph['\"]\s*,\s*version\s*:\s*['\"]([^'\"]+)['\"]", text)
    if not m:
        raise ValueError(f"Could not find project version in {path}")
    return m.group(1).strip()


def get_metainfo_version() -> str:
    path = ROOT / "data/io.github.the0megastar.Glyph.metainfo.xml"
    tree = ET.parse(path)
    root = tree.getroot()
    releases = root.find("releases")
    if releases is None or not len(releases):
        raise ValueError(f"No releases found in {path}")
    first_release = releases.find("release")
    if first_release is None or "version" not in first_release.attrib:
        raise ValueError(f"First release has no version attribute in {path}")
    return first_release.attrib["version"].strip()


def get_rpm_spec_version() -> str:
    path = ROOT / "packaging/rpm/glyph.spec"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    return ""


def get_aur_pkgbuild_version() -> str:
    path = ROOT / "packaging/aur/PKGBUILD"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("pkgver="):
            return line.split("=", 1)[1].strip()
    return ""


def validate_versions(expected_tag: str | None = None) -> bool:
    versions = {
        "src/glyph/__init__.py": get_init_version(),
        "meson.build": get_meson_version(),
        "data/io.github.the0megastar.Glyph.metainfo.xml": get_metainfo_version(),
        "packaging/rpm/glyph.spec": get_rpm_spec_version(),
        "packaging/aur/PKGBUILD": get_aur_pkgbuild_version(),
    }

    print("=== Version Consistency Check ===")
    all_match = True
    base_version = versions["src/glyph/__init__.py"]

    for source, ver in versions.items():
        if not ver:
            print(f"  [MISSING] {source}")
            all_match = False
            continue
        status = "OK" if ver == base_version else "MISMATCH"
        if status != "OK":
            all_match = False
        print(f"  [{status}] {source:45s} -> {ver}")

    if expected_tag:
        clean_tag = expected_tag.lstrip("v")
        status = "OK" if clean_tag == base_version else "MISMATCH"
        if status != "OK":
            all_match = False
        print(f"  [{status}] Git Tag ({expected_tag:36s}) -> {clean_tag}")

    return all_match


def validate_metadata() -> bool:
    print("\n=== Desktop and AppStream Validation ===")
    all_ok = True

    desktop_file = ROOT / "data/io.github.the0megastar.Glyph.desktop"
    if shutil.which("desktop-file-validate"):
        res = subprocess.run(["desktop-file-validate", str(desktop_file)], capture_output=True, text=True)
        if res.returncode == 0:
            print("  [OK] desktop-file-validate passed")
        else:
            print(f"  [FAIL] desktop-file-validate: {res.stderr}")
            all_ok = False
    else:
        print("  [SKIP] desktop-file-validate not installed")

    metainfo_file = ROOT / "data/io.github.the0megastar.Glyph.metainfo.xml"
    if shutil.which("appstreamcli"):
        res = subprocess.run(
            ["appstreamcli", "validate", "--no-net", str(metainfo_file)],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            print("  [OK] appstreamcli validate passed")
        else:
            print(f"  [FAIL] appstreamcli validate: {res.stderr or res.stdout}")
            all_ok = False
    elif shutil.which("appstream-util"):
        res = subprocess.run(
            ["appstream-util", "validate-relax", str(metainfo_file)],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            print("  [OK] appstream-util validate-relax passed")
        else:
            print(f"  [FAIL] appstream-util validate-relax: {res.stderr or res.stdout}")
            all_ok = False
    else:
        print("  [SKIP] appstreamcli / appstream-util not installed")

    return all_ok


def validate_release_notes() -> bool:
    print("\n=== Release Notes Validation ===")
    version = get_init_version()
    possible_paths = [
        ROOT / "releases" / f"v{version}.md",
        ROOT / "releases" / f"{version}.md",
    ]
    for path in possible_paths:
        if path.is_file() and path.stat().st_size > 0:
            print(f"  [OK] Release notes found: {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
            return True

    print(f"  [FAIL] Missing release notes: releases/v{version}.md")
    print(f"         Please create releases/v{version}.md before releasing.")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Glyph release consistency")
    parser.add_argument("--tag", help="Optional git tag to match (e.g. v0.1.3)")
    args = parser.parse_args()

    v_ok = validate_versions(args.tag)
    m_ok = validate_metadata()
    r_ok = validate_release_notes()

    if v_ok and m_ok and r_ok:
        print("\nAll release validation checks PASSED.")
        return 0
    else:
        print("\nRelease validation FAILED.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
