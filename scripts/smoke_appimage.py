"""Run through AppRun in a clean display-enabled container, never host Python."""
import base64
from pathlib import Path
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Adw, GdkPixbuf, Gio, GLib, Gtk
from glyph.main import GlyphApplication

root = Path(__file__).resolve().parents[2]
assert Path(sys.executable).is_relative_to(root), sys.executable
assert Path(gi.__file__).is_relative_to(root), gi.__file__
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 10)
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5)
assert Gio.SettingsSchemaSource.get_default().lookup('io.github.the0megastar.Glyph', True)
formats = {fmt.get_name() for fmt in GdkPixbuf.Pixbuf.get_formats()}
assert {'png', 'jpeg', 'svg', 'webp'} <= formats, formats
images = [
    base64.b64decode('UklGRjwAAABXRUJQVlA4IDAAAADQAQCdASoBAAEAAUAmJaACdLoB+AADsAD+8ut//NgVzXPv9//S4P0uD9Lg/9KQAAA='),
    base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='),
    b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="red"/></svg>',
]
for data in images:
    loader = GdkPixbuf.PixbufLoader.new()
    loader.write(data)
    loader.close()
    assert loader.get_pixbuf() is not None
# Exercise the JPEG encoder and decoder without needing an external fixture.
_, jpeg = loader.get_pixbuf().save_to_bufferv('jpeg', [], [])
loader = GdkPixbuf.PixbufLoader.new_with_type('jpeg')
loader.write(jpeg)
loader.close()
assert loader.get_pixbuf() is not None

Adw.init()
application = GlyphApplication()
errors = []


def check_window():
    try:
        window = application.props.active_window
        assert window is not None and window.get_visible(), 'Glyph window did not open'
        application.on_preferences()
        application.on_about()
    except Exception as exc:
        errors.append(exc)
    finally:
        application.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(1000, check_window)
assert application.run(['glyph']) == 0
if errors:
    raise errors[0]
print('AppImage smoke test passed: bundled Python, GTK window, settings and image loaders.')
