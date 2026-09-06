python = import('python').find_installation('python3')

python.install_sources(
  [
    'glyph/__init__.py',
    'glyph/__main__.py',
    'glyph/main.py',
    'glyph/window.py',
    'glyph/catalog.py',
    'glyph/overrides.py',
    'glyph/paths.py',
  ],
  preserve_path: true,
)

configure_file(
  input: 'glyph.in',
  output: 'glyph',
  copy: true,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)
