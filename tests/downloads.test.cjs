const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolveReleaseDownloads: resolve, renderReleaseDownloads: render } = require('../app.js');
const names = ['Glyph-0.1.3-x86_64.flatpak', 'Glyph-0.1.3-aarch64.flatpak', 'Glyph-0.1.3-x86_64.AppImage', 'Glyph-0.1.3-aarch64.AppImage', 'glyph-0.1.3.deb', 'glyph-0.1.3.rpm', 'glyph-0.1.3-1-any.pkg.tar.zst'];
const asset = name => ({ name, browser_download_url: `https://github.com/the0megastar/Glyph/releases/download/v0.1.3/${name}` });
const release = assets => ({ tag_name: 'v0.1.3', assets });
const complete = resolve(release(names.map(asset)));
test('seven exact mappings do not depend on asset order', () => {
  assert.deepEqual(Object.values(complete), names.map(name => asset(name).browser_download_url));
  assert.deepEqual(resolve(release(names.map(asset).reverse())), complete);
});
test('legacy, missing, unrelated, duplicate and malformed assets never select a binary', () => {
  const result = resolve(release([asset('Glyph-0.1.3.AppImage'), null, {}, asset(names[0]), asset(names[0]), asset(names[2] + '.sha256'), asset('Glyph-0.1.2-aarch64.AppImage')]));
  assert.ok(Object.values(result).every(value => value === null));
  assert.ok(Object.values(resolve(release())).every(value => value === null));
  assert.throws(() => resolve({ tag_name: null }));
});
test('download URLs must identify the exact release asset in this repository', () => {
  for (const url of ['https://example.com/file', 'javascript:alert(1)', asset(names[1]).browser_download_url, asset(names[0]).browser_download_url + '?other=1']) {
    assert.equal(resolve(release([{ name: names[0], browser_download_url: url }])).downloadFlatpakX86, null);
  }
});
test('rendering clears stale links and restores the primary fallback', () => {
  const elements = {};
  for (const id of [...Object.keys(complete), 'primaryDownloadBtn', 'downloadStatus']) {
    const child = { textContent: 'Package description' };
    elements[id] = { dataset: {}, querySelector: () => child, setAttribute(k,v) { this[k] = v; }, removeAttribute(k) { delete this[k]; } };
  }
  const root = { getElementById: id => elements[id] };
  render(complete, root);
  assert.equal(elements.primaryDownloadBtn.href, complete.downloadFlatpakX86);
  render(resolve(release([])), root);
  assert.equal(elements.downloadFlatpakX86.href, undefined);
  assert.equal(elements.downloadFlatpakX86['aria-disabled'], 'true');
  assert.ok(elements.primaryDownloadBtn.href.endsWith('/releases/latest'));
  render(null, root);
  assert.ok(elements.downloadAppImageArm.href.endsWith('/releases/latest'));
  assert.equal(elements.downloadAppImageArm['aria-disabled'], undefined);
});
test('HTML exposes every slot once and contains no versioned latest-download fallback', () => {
  const html = readFileSync('index.html', 'utf8');
  for (const id of Object.keys(complete)) assert.equal(html.split(`id="${id}"`).length - 1, 1);
  assert.ok(!html.includes('id="downloadAppImage"'));
  assert.ok(!html.includes('/latest/download/'));
  assert.ok(html.includes('id="downloadStatus"'));
});
test('fetch failures and timeouts render fallback and clear their timer', async () => {
  const { runInNewContext } = require('node:vm');
  const source = readFileSync('app.js', 'utf8');
  for (const kind of ['network', 'http', 'json', 'timeout']) {
    let cleared = false;
    const rendered = [];
    const context = { module: { exports: {} }, URL, AbortController,
      setTimeout: callback => { if (kind === 'timeout') callback(); return 1; },
      clearTimeout: () => { cleared = true; },
      fetch: async (_url, options) => {
        if (kind === 'network' || options.signal.aborted) throw new Error(kind);
        return { ok: kind !== 'http', json: async () => { throw new Error('invalid JSON'); } };
      }
    };
    runInNewContext(source, context);
    context.renderReleaseDownloads = value => rendered.push(value);
    await context.fetchLatestRelease();
    assert.deepEqual(rendered, [null]);
    assert.ok(cleared);
  }
});
