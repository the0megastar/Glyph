install_data(
  'io.github.the0megastar.Glyph.desktop',
  install_dir: get_option('datadir') / 'applications',
)

install_data(
  'icons/io.github.the0megastar.Glyph.svg',
  install_dir: get_option('datadir') / 'icons' / 'hicolor' / 'scalable' / 'apps',
)

install_data(
  'io.github.the0megastar.Glyph.metainfo.xml',
  install_dir: get_option('datadir') / 'metainfo',
)

gnome = import('gnome')
gnome.compile_schemas(
  depend_files: files('io.github.the0megastar.Glyph.gschema.xml'),
)

install_data(
  'io.github.the0megastar.Glyph.gschema.xml',
  install_dir: get_option('datadir') / 'glib-2.0' / 'schemas',
)

gnome.post_install(glib_compile_schemas: true)
