"""Desktop-file text manipulation, parsing, and key serialization."""
from __future__ import annotations

import re


class DesktopTextError(Exception):
    """Raised when desktop file text syntax is invalid."""


def _snapshot_lines(text: str, key: str) -> list[str]:
    inside = False
    result = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            inside = stripped == '[Desktop Entry]'
        elif inside and re.match(r'^' + key + r'(?:\[[^\]]+\])?\s*=', stripped):
            result.append(line if line.endswith('\n') else line + '\n')
    return result


def _replace_lines(text: str, key: str, replacement: list[str]) -> str:
    inside = False
    found_group = False
    out = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            inside = stripped == '[Desktop Entry]'
            out.append(line if line.endswith('\n') else line + '\n')
            if inside:
                if found_group:
                    from glyph.overrides import OverrideError
                    raise OverrideError('Desktop file contains duplicate Desktop Entry groups.')
                found_group = True
                out.extend(replacement)
        elif inside and re.match(r'^' + key + r'(?:\[[^\]]+\])?\s*=', stripped):
            continue
        else:
            out.append(line if line.endswith('\n') else line + '\n')
    if not found_group:
        from glyph.overrides import OverrideError
        raise OverrideError('Desktop file is missing a [Desktop Entry] group.')
    return ''.join(out)


def _escape(val: str) -> str:
    return val.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def _value(text: str, key: str) -> str:
    for line in _snapshot_lines(text, key):
        if line.split('=', 1)[0].strip() == key:
            value = line.split('=', 1)[1].strip()
            return re.sub(r'\\([snrt\\])', lambda m: {'s': ' ', 'n': '\n', 'r': '\r', 't': '\t', '\\': '\\'}[m[1]], value)
    return ''


def get_icon_value(text: str) -> str:
    return _value(text, 'Icon')


def get_name_value(text: str) -> str:
    return _value(text, 'Name')


def set_icon_value(text: str, value: str) -> str:
    return _replace_lines(text, 'Icon', [f'Icon={_escape(value)}\n'])


def set_name_value(text: str, value: str) -> str:
    return _replace_lines(text, 'Name', [f'Name={_escape(value)}\n'])

