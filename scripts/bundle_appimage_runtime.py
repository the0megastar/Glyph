"""Stage a relocatable runtime from the native Ubuntu 24.04 build environment."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import sysconfig

import cairo
import gi

gi.require_version('GIRepository', '2.0')
from gi.repository import GIRepository

appdir = Path(sys.argv[1]).resolve()
lib = appdir / 'usr/lib'
pending = []
copied = set()
elf_files = []


def copy(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination, follow_symlinks=True)
    with source.open('rb') as stream:
        if stream.read(4) == b'\x7fELF':
            pending.append(source)
            elf_files.append(destination)


def tree(source, destination):
    source = Path(source)
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        if any(part in {'__pycache__', 'site-packages', 'dist-packages'} for part in relative.parts):
            continue
        if path.is_file():
            copy(path, destination / relative)


# Dereference the interpreter and package files so no links point back to /usr.
copy(sys.executable, appdir / 'usr/bin/python3')
tree(sysconfig.get_path('stdlib'), lib / f'python{sys.version_info.major}.{sys.version_info.minor}')
for module in (gi, cairo):
    tree(Path(module.__file__).parent, lib / 'python3/dist-packages' / module.__name__)

# Typelibs load libraries with dlopen, so ldd alone cannot discover this closure.
repository = GIRepository.Repository.get_default()
for namespace, version in [('Gtk', '4.0'), ('Adw', '1'), ('GioUnix', '2.0'), ('GdkPixbuf', '2.0')]:
    repository.require(namespace, version, 0)
libraries = {}
for line in subprocess.check_output(['/sbin/ldconfig', '-p'], text=True).splitlines():
    match = re.match(r'\s*(\S+) .* => (/.+)', line)
    if match:
        libraries.setdefault(match[1], Path(match[2]))
for namespace in repository.get_loaded_namespaces():
    typelib = Path(repository.get_typelib_path(namespace))
    copy(typelib, lib / 'girepository-1.0' / typelib.name)
    for name in (repository.get_shared_library(namespace) or '').split(','):
        if name:
            source = libraries[name]
            copy(source, lib / name)
            copied.add(name)

# Preserve all installed pixbuf formats, including SVG and WebP.
multiarch = sysconfig.get_config_var('MULTIARCH')
pixbuf = Path('/usr/lib') / multiarch / 'gdk-pixbuf-2.0'
loaders = pixbuf / '2.10.0/loaders'
if not list(loaders.glob('*.so')):
    raise SystemExit('No GdkPixbuf image loaders installed')
tree(loaders, lib / 'gdk-pixbuf-2.0/2.10.0/loaders')
cache = subprocess.check_output([str(pixbuf / 'gdk-pixbuf-query-loaders'),
                                 *map(str, sorted(loaders.glob('*.so')))], text=True)
cache = cache.replace(str(loaders), '@APPDIR@/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders')
(lib / 'loaders.cache.in').write_text(cache)

# GSettings persistence is supplied by a dynamically loaded GIO backend.
gio_modules = Path('/usr/lib') / multiarch / 'gio/modules'
tree(gio_modules, lib / 'gio/modules')
glib_tools = Path('/usr/lib') / multiarch / 'glib-2.0'
subprocess.run([str(glib_tools / 'gio-querymodules'), str(lib / 'gio/modules')], check=True)

# Leave glibc and GPU dispatch/drivers to the host. Bundle the remaining ELF
# dependency closure, including Python extension and image-loader dependencies.
host_libraries = re.compile(r'^(?:ld-linux.*|lib(?:c|m|dl|pthread|rt|resolv|util|anl)\.so\..*|'
                            r'lib(?:GL|EGL|GLX|GLdispatch|OpenGL|vulkan)\.so\..*)$')
seen = set()
while pending:
    source = pending.pop()
    if source.resolve() in seen:
        continue
    seen.add(source.resolve())
    output = subprocess.check_output(['ldd', str(source)], text=True)
    if 'not found' in output:
        raise SystemExit(f'Unresolved dependency for {source}:\n{output}')
    for name, filename in re.findall(r'(\S+) => (/\S+) \(', output):
        if name not in copied and not host_libraries.fullmatch(name):
            copied.add(name)
            copy(filename, lib / name)

# Use per-file lookup paths instead of exporting LD_LIBRARY_PATH to applications
# launched from Glyph. Every extension and dlopen caller can find the bundle.
for path in elf_files:
    relative = os.path.relpath(lib, path.parent)
    subprocess.run(['patchelf', '--set-rpath', f'$ORIGIN/{relative}', str(path)], check=True)

share = appdir / 'usr/share'
for directory in ('glib-2.0/schemas', 'mime', 'icons/Adwaita', 'icons/hicolor'):
    tree(Path('/usr/share') / directory, share / directory)
copy('data/io.github.the0megastar.Glyph.gschema.xml',
     share / 'glib-2.0/schemas/io.github.the0megastar.Glyph.gschema.xml')
subprocess.run([str(glib_tools / 'glib-compile-schemas'), str(share / 'glib-2.0/schemas')], check=True)

# Include Ubuntu's copyright/license notices for the redistributed components.
for notice in Path('/usr/share/doc').glob('*/copyright'):
    copy(notice, share / 'doc' / notice.parent.name / 'copyright')
tree('/usr/share/common-licenses', share / 'common-licenses')
print(f'Bundled Python {sys.version.split()[0]}, {len(copied)} shared libraries and image loaders.')
