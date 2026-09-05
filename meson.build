project(
  'glyph',
  version: '0.1.1',
  meson_version: '>= 0.63.0',
)

dependency('gtk4', version: '>= 4.10', required: false)
dependency('libadwaita-1', version: '>= 1.4', required: false)
dependency('pygobject-3.0', required: false)

python = import('python').find_installation('python3')

meson.add_devenv({'PYTHONPATH': meson.project_source_root() / 'src'})

subdir('src')
subdir('data')
