#!/usr/bin/env bash
# Run inside a disposable Arch container as root, with a pre-created builder user.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
output=${1:?Usage: build_arch.sh OUTPUT_DIRECTORY}
mkdir -p "$output"
output=$(realpath "$output")
stage=$(mktemp -d /tmp/glyph-arch.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
cp "$root/packaging/aur/PKGBUILD" "$stage/PKGBUILD"
version=$(sed -n 's/^pkgver=//p' "$stage/PKGBUILD")
revision=$(sed -n 's/^pkgrel=//p' "$stage/PKGBUILD")
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && $revision =~ ^[0-9]+(\.[0-9]+)?$ ]]
archive="glyph-${version}.tar.gz"
git config --global --add safe.directory '*' 2>/dev/null || true

if [ -d "$root/.git" ] && git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$root" archive --format=tar --prefix="glyph-${version}/" HEAD | gzip -n > "$stage/$archive"
  source_desc="$(git -C "$root" rev-parse HEAD 2>/dev/null || echo 'git-HEAD')"
else
  tar --exclude='.git*' \
      --exclude='_build*' \
      --exclude='*.pyc' \
      --exclude='__pycache__' \
      --exclude='.flatpak*' \
      --exclude='arch-output*' \
      --exclude='.vscode' \
      --exclude='docs' \
      --transform "s,^\.,glyph-${version}," \
      -czf "$stage/$archive" -C "$root" .
  source_desc="workspace-archive"
fi
python3 - "$stage" "$archive" <<'PY'
import hashlib
from pathlib import Path
import re
import sys
stage, archive = Path(sys.argv[1]), sys.argv[2]
recipe = stage / 'PKGBUILD'
text = recipe.read_text()
digest = hashlib.sha256((stage / archive).read_bytes()).hexdigest()
for pattern, replacement in [
    (r'^source=\([^\n]*\)$', f"source=('{archive}')"),
    (r'^sha256sums=\([^\n]*\)$', f"sha256sums=('{digest}')"),
]:
    text, count = re.subn(pattern, replacement, text, flags=re.M)
    if count != 1:
        raise SystemExit('Expected exactly one source/checksum declaration')
recipe.write_text(text)
print(f'Staged source SHA256: {digest}')
PY
printf 'Building source: %s\n' "$source_desc"
chown -R builder:builder "$stage"
runuser -u builder -- bash -c 'cd "$1"; PKGEXT=.pkg.tar.zst makepkg --cleanbuild --check --noconfirm' bash "$stage"
expected="glyph-${version}-${revision}-any.pkg.tar.zst"
mapfile -t packages < <(find "$stage" -maxdepth 1 -name '*.pkg.tar.zst' -type f)
[[ ${#packages[@]} -eq 1 && ${packages[0]##*/} == "$expected" ]]
test -s "$stage/$expected"
metadata=$(bsdtar -xOf "$stage/$expected" .PKGINFO)
for field in "pkgname = glyph" "pkgver = ${version}-${revision}" 'arch = any'; do
  grep -Fxq "$field" <<< "$metadata"
done
install -m 644 "$stage/$expected" "$output/$expected"
printf 'package=%s\n' "$expected" >> "${GITHUB_OUTPUT:-/dev/null}"
