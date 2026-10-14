project(
  'glyph',
  version: '0.1.5',
  meson_version: '>= 0.63.0',
)

dependency('gtk4', version: '>= 4.10', required: true)
dependency('libadwaita-1', version: '>= 1.5', required: true)
dependency('pygobject-3.0', required: true)

python = import('python').find_installation('python3')

meson.add_devenv({'PYTHONPATH': meson.project_source_root() / 'src'})

subdir('src')
subdir('data')

test_env = {'PYTHONPATH': meson.project_source_root() / 'src'}
test('test-paths', python, args: [files('tests/test_paths.py')], env: test_env)
test('test-overrides', python, args: [files('tests/test_overrides.py')], env: test_env)
test('test-catalog', python, args: [files('tests/test_catalog.py')], env: test_env)
test('test-sources', python, args: [files('tests/test_sources.py')], env: test_env)
test('test-icon-themes', python, args: [files('tests/test_icon_themes.py')], env: test_env)
test('test-launcher', python, args: [files('tests/test_launcher.py')], env: test_env)
test('test-appimage', python, args: [files('tests/test_appimage.py')], env: test_env)
test('test-ui', python, args: [files('tests/test_ui.py')], env: test_env)
