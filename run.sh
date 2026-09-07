#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"

schema_cache="${XDG_CACHE_HOME:-${HOME}/.cache}/glyph/schemas"
mkdir -p "$schema_cache"
cp "$root/data/io.github.the0megastar.Glyph.gschema.xml" "$schema_cache/"
glib-compile-schemas "$schema_cache"
export GSETTINGS_SCHEMA_DIR="$schema_cache"

exec python3 -m glyph "$@"
