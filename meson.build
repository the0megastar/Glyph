project(
  'glyph',
  version: '0.1.3',
  meson_version: '>= 0.63.0',
)

dependency('gtk4', version: '>= 4.10', required: true)
dependency('libadwaita-1', version: '>= 1.5', required: true)
dependency('pygobject-3.0', required: true)

python = import('python').find_installation('python3')

meson.add_devenv({'PYTHONPATH': meson.project_source_root() / 'src'})

subdir('src')
subdir('data')
