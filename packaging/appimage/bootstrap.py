"""Resolve image loader paths after the AppImage is mounted or relocated."""
import os
from pathlib import Path
import runpy
import sys
import tempfile

appdir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(appdir / 'usr/lib/python3/dist-packages'))
with tempfile.TemporaryDirectory(prefix='glyph-loaders-') as temporary:
    cache = Path(temporary) / 'loaders.cache'
    # GdkPixbuf's cache uses quoted C strings for absolute module filenames.
    escaped = str(appdir).replace('\\', '\\\\').replace('"', '\\"')
    cache.write_text((appdir / 'usr/lib/loaders.cache.in').read_text().replace('@APPDIR@', escaped))
    os.environ['GDK_PIXBUF_MODULE_FILE'] = str(cache)
    if sys.argv[1:] == ['--appimage-smoke-test']:
        runpy.run_path(str(appdir / 'usr/bin/glyph-smoke.py'), run_name='__main__')
    else:
        from glyph.main import main
        sys.exit(main([str(appdir / 'usr/bin/glyph'), *sys.argv[1:]]))
