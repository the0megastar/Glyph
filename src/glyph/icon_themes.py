"""Installed icon themes enumeration, searching, and preview resolution."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from gi.repository import GLib

import glyph.paths as paths


@dataclass(frozen=True)
class ThemeSummary:
    theme_id: str
    name: str
    comment: str
    inherits: tuple[str, ...]
    path: str
    hidden: bool


@dataclass(frozen=True)
class ThemeIcon:
    name: str
    theme_id: str
    context: str
    file_path: str
    is_scalable: bool


_theme_cache: dict[str, ThemeSummary] | None = None
_icon_cache: dict[str, list[ThemeIcon]] = {}


def clear_theme_caches() -> None:
    global _theme_cache, _icon_cache
    _theme_cache = None
    _icon_cache.clear()


def list_installed_themes() -> list[ThemeSummary]:
    """Enumerate installed icon themes from search roots."""
    global _theme_cache
    if _theme_cache is not None:
        return list(_theme_cache.values())

    discovered: dict[str, ThemeSummary] = {}
    for root in paths.icon_dirs():
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue

        for entry in entries:
            theme_id = entry.name
            if theme_id.startswith(".") or theme_id in discovered:
                continue

            if entry.is_symlink():
                try:
                    resolved_entry = entry.resolve()
                    if not resolved_entry.is_dir():
                        continue
                except (OSError, RuntimeError):
                    continue
            elif not entry.is_dir():
                continue

            index_file = entry / "index.theme"
            try:
                if not index_file.is_file():
                    continue
            except OSError:
                continue

            kf = GLib.KeyFile()
            try:
                kf.load_from_file(str(index_file), GLib.KeyFileFlags.NONE)
                try:
                    display_name = kf.get_string("Icon Theme", "Name") or theme_id
                except GLib.Error:
                    display_name = theme_id
                try:
                    comment = kf.get_string("Icon Theme", "Comment") or ""
                except GLib.Error:
                    comment = ""
                try:
                    inherits_raw = kf.get_string_list("Icon Theme", "Inherits") or []
                    inherits = tuple(v for v in inherits_raw if v)
                except GLib.Error:
                    inherits = ()
                try:
                    hidden = kf.get_boolean("Icon Theme", "Hidden")
                except GLib.Error:
                    hidden = False

                discovered[theme_id] = ThemeSummary(
                    theme_id=theme_id,
                    name=display_name,
                    comment=comment,
                    inherits=inherits,
                    path=str(entry),
                    hidden=hidden,
                )
            except GLib.Error:
                continue

    # Order by user-visible display name; keep hicolor and Adwaita readily accessible
    ordered = sorted(
        discovered.values(),
        key=lambda t: (t.hidden, t.name.casefold()),
    )
    _theme_cache = {t.theme_id: t for t in ordered}
    return ordered


def _score_icon_variant(path: Path) -> int:
    """Score a candidate icon path to select the best visual quality."""
    suffix = path.suffix.lower()
    score = 0
    if suffix == ".svg":
        score += 1000
    elif suffix == ".png":
        score += 500
        # Check size if available in path
        for part in path.parts:
            m = re.match(r"^(\d+)(?:x\1)?$", part)
            if m:
                try:
                    size = int(m.group(1))
                    score += min(size, 256)
                except ValueError:
                    pass
    if "scalable" in path.parts:
        score += 200
    if "apps" in path.parts:
        score += 100
    return score


def list_theme_icons(theme_id: str) -> list[ThemeIcon]:
    """Scan and index all distinct icons available in a theme and its inheritance chain."""
    if theme_id in _icon_cache:
        return _icon_cache[theme_id]

    themes = {t.theme_id: t for t in list_installed_themes()}
    target_theme = themes.get(theme_id)
    if not target_theme:
        return []

    # Gather themes in inheritance order: target theme first, then inherited
    themes_to_scan = [target_theme]
    for inherit_id in target_theme.inherits:
        if inherit_id in themes and themes[inherit_id] not in themes_to_scan:
            themes_to_scan.append(themes[inherit_id])
    if "hicolor" in themes and themes["hicolor"] not in themes_to_scan:
        themes_to_scan.append(themes["hicolor"])

    # Map icon_name -> (best_score, ThemeIcon)
    best_icons: dict[str, tuple[int, ThemeIcon]] = {}

    for current_theme in themes_to_scan:
        theme_root = Path(current_theme.path)
        if not theme_root.is_dir():
            continue

        visited_dirs: set[Path] = set()

        def scan_dir(dir_path: Path, depth: int) -> None:
            if depth > 4:
                return
            try:
                resolved = dir_path.resolve()
            except OSError:
                return
            if resolved in visited_dirs:
                return
            visited_dirs.add(resolved)

            try:
                for entry in dir_path.iterdir():
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir():
                        scan_dir(entry, depth + 1)
                    elif entry.is_file() and entry.suffix.lower() in (".svg", ".png", ".webp"):
                        icon_name = entry.stem
                        if not icon_name:
                            continue
                        # Context detection (apps, actions, categories, etc.)
                        parts = entry.relative_to(theme_root).parts
                        context = "apps" if "apps" in parts else (parts[0] if len(parts) > 1 else "generic")
                        score = _score_icon_variant(entry)
                        # Inherited icons have lower priority than base theme icons
                        if current_theme.theme_id != theme_id:
                            score -= 10000

                        existing = best_icons.get(icon_name)
                        if existing is None or score > existing[0]:
                            is_scalable = entry.suffix.lower() == ".svg" or "scalable" in parts
                            best_icons[icon_name] = (
                                score,
                                ThemeIcon(
                                    name=icon_name,
                                    theme_id=current_theme.theme_id,
                                    context=context,
                                    file_path=str(entry),
                                    is_scalable=is_scalable,
                                    ),
                                )
            except OSError:
                pass

        scan_dir(theme_root, 0)

    # Sort app icons first, then by name
    sorted_icons = sorted(
        (item[1] for item in best_icons.values()),
        key=lambda i: (0 if i.context == "apps" else 1, i.name.casefold()),
    )
    _icon_cache[theme_id] = sorted_icons
    return sorted_icons


def search_theme_icons(theme_id: str, query: str, limit: int = 150) -> list[ThemeIcon]:
    """Search for icons in a theme matching a query with relevance sorting."""
    icons = list_theme_icons(theme_id)
    clean_query = query.strip().casefold()
    if not clean_query:
        return icons[:limit]

    results: list[tuple[int, ThemeIcon]] = []
    for icon in icons:
        name_lower = icon.name.casefold()
        if name_lower == clean_query:
            score = 0
        elif name_lower.startswith(clean_query):
            score = 1
        elif clean_query in name_lower:
            score = 2
        else:
            continue
        # Favor app context
        context_bonus = 0 if icon.context == "apps" else 1
        results.append((score * 10 + context_bonus, icon))

    results.sort(key=lambda item: (item[0], item[1].name.casefold()))
    return [item[1] for item in results[:limit]]
