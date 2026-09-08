// Glyph Website Interactive Logic

if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', () => {
  initThemeToggle();
  initCopyButtons();
  initDownloadDropdown();
  fetchLatestRelease();
});

const RELEASE_PAGE = 'https://github.com/the0megastar/Glyph/releases/latest';
const DOWNLOADS = {
  downloadFlatpakX86: v => `Glyph-${v}-x86_64.flatpak`,
  downloadFlatpakArm: v => `Glyph-${v}-aarch64.flatpak`,
  downloadAppImageX86: v => `Glyph-${v}-x86_64.AppImage`,
  downloadAppImageArm: v => `Glyph-${v}-aarch64.AppImage`,
  downloadDeb: v => `glyph-${v}.deb`,
  downloadRpm: v => `glyph-${v}.rpm`,
  downloadArch: v => new RegExp(`^glyph-${v.replace(/\./g, '\\.')}-\\d+(?:\\.\\d+)?-any\\.pkg\\.tar\\.zst$`),
};

function resolveReleaseDownloads(release) {
  if (typeof release?.tag_name !== 'string' || !/^v?\d+\.\d+\.\d+[\w.+-]*$/.test(release.tag_name)) {
    throw new Error('Invalid release');
  }
  const version = release.tag_name.replace(/^v/, '');
  const assets = Array.isArray(release.assets) ? release.assets : [];
  return Object.fromEntries(Object.entries(DOWNLOADS).map(([id, matcher]) => {
    const pattern = matcher(version);
    const matches = assets.filter(asset => {
      if (!asset?.name) return false;
      return typeof pattern === 'string' ? asset.name === pattern : pattern.test(asset.name);
    });
    const asset = matches.length === 1 ? matches[0] : null;
    let href = null;
    try {
      const url = new URL(asset?.browser_download_url);
      const expectedPath = `/the0megastar/Glyph/releases/download/${release.tag_name}/${asset.name}`;
      if (url.origin === 'https://github.com' && !url.username && !url.password &&
          decodeURIComponent(url.pathname) === expectedPath && !url.search && !url.hash) href = url.href;
    } catch { /* Missing or malformed assets remain unavailable. */ }
    return [id, href];
  }));
}

function renderReleaseDownloads(downloads, root = document) {
  for (const id of Object.keys(DOWNLOADS)) {
    const row = root.getElementById(id);
    if (!row) continue;
    const subtitle = row.querySelector('.dropdown-item-sub');
    if (!row.dataset.description) row.dataset.description = subtitle.textContent;
    const href = downloads?.[id];
    if (href || !downloads) {
      row.href = href || RELEASE_PAGE;
      row.removeAttribute('aria-disabled');
      row.removeAttribute('tabindex');
    } else {
      row.removeAttribute('href');
      row.setAttribute('aria-disabled', 'true');
      row.setAttribute('tabindex', '-1');
    }
    subtitle.textContent = href ? row.dataset.description : downloads ?
      'Not available in this release' : 'View release assets on GitHub';
  }
  const primary = root.getElementById('primaryDownloadBtn');
  const href = downloads?.downloadFlatpakX86;
  primary.href = href || RELEASE_PAGE;
  primary.querySelector('span').textContent = href ? 'Download Flatpak · x86_64' : 'View Linux downloads';
  primary.removeAttribute('aria-label');
  root.getElementById('downloadStatus').textContent = !downloads ?
    'Direct downloads could not be checked. View release assets on GitHub.' :
    Object.values(downloads).some(url => !url) ?
      'Some packages are unavailable. Older assets may be listed on GitHub.' : '';
}

if (typeof module !== 'undefined') module.exports = { resolveReleaseDownloads, renderReleaseDownloads };

/* 1. Dark / Light Theme Toggle */
function initThemeToggle() {
  const themeToggleBtn = document.getElementById('themeToggleBtn');
  if (!themeToggleBtn) return;

  const sunIcon = themeToggleBtn.querySelector('.sun-icon');
  const moonIcon = themeToggleBtn.querySelector('.moon-icon');

  function getPreferredTheme() {
    const saved = localStorage.getItem('glyph-theme');
    if (saved) return saved;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('glyph-theme', theme);

    if (theme === 'light') {
      sunIcon.style.display = 'block';
      moonIcon.style.display = 'none';
      themeToggleBtn.setAttribute('title', 'Switch to dark theme');
    } else {
      sunIcon.style.display = 'none';
      moonIcon.style.display = 'block';
      themeToggleBtn.setAttribute('title', 'Switch to light theme');
    }
  }

  applyTheme(getPreferredTheme());

  themeToggleBtn.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
  });

  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
    if (!localStorage.getItem('glyph-theme')) {
      applyTheme(e.matches ? 'light' : 'dark');
    }
  });
}

/* 2. Download Split Button & Dropdown Menu */
function initDownloadDropdown() {
  const splitGroup = document.getElementById('downloadSplitGroup');
  const toggleBtn = document.getElementById('downloadDropdownToggle');
  const dropdownMenu = document.getElementById('downloadDropdownMenu');
  const archAurDropdownItem = document.getElementById('archAurDropdownItem');

  if (!splitGroup || !toggleBtn || !dropdownMenu) return;

  function openDropdown() {
    dropdownMenu.classList.add('open');
    splitGroup.classList.add('dropdown-open');
    toggleBtn.setAttribute('aria-expanded', 'true');
  }

  function closeDropdown() {
    const returnFocus = dropdownMenu.contains(document.activeElement);
    dropdownMenu.classList.remove('open');
    splitGroup.classList.remove('dropdown-open');
    toggleBtn.setAttribute('aria-expanded', 'false');
    if (returnFocus) toggleBtn.focus();
  }

  toggleBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = dropdownMenu.classList.contains('open');
    if (isOpen) {
      closeDropdown();
    } else {
      openDropdown();
    }
  });

  // Close when clicking outside
  document.addEventListener('click', (e) => {
    if (!splitGroup.contains(e.target)) {
      closeDropdown();
    }
  });

  // Close on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeDropdown();
    }
  });

  // Arch AUR item click handler
  if (archAurDropdownItem) {
    archAurDropdownItem.addEventListener('click', (e) => {
      e.preventDefault();
      closeDropdown();
      const archCard = document.getElementById('archInstallCard') || document.getElementById('install');
      if (archCard) {
        archCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });
  }
}

/* 3. Command Box Copy Functionality */
function initCopyButtons() {
  function attachCopy(btnId, textId, labelId) {
    const btn = document.getElementById(btnId);
    const textEl = document.getElementById(textId);
    const label = document.getElementById(labelId);
    if (!btn || !textEl) return;

    btn.addEventListener('click', async () => {
      const textToCopy = textEl.textContent.trim();
      try {
        await navigator.clipboard.writeText(textToCopy);
      } catch {
        const textarea = document.createElement('textarea');
        textarea.value = textToCopy;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        try { document.execCommand('copy'); } finally { document.body.removeChild(textarea); }
      }
      btn.classList.add('copied');
      if (label) label.textContent = 'Copied!';
      setTimeout(() => {
        btn.classList.remove('copied');
        if (label) label.textContent = 'Copy';
      }, 2200);
    });
  }

  attachCopy('copyBtn', 'commandText', 'copyBtnLabel');
  attachCopy('archCopyBtn', 'archCommandText', 'archCopyBtnLabel');
}

/* 5. Progressive Enhancement: Latest GitHub Release Check & Dynamic Release Notes */
async function fetchLatestRelease() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const res = await fetch('https://api.github.com/repos/the0megastar/Glyph/releases/latest', { signal: controller.signal });
    if (!res.ok) throw new Error('Release request failed');
    const data = await res.json();
    renderReleaseDownloads(resolveReleaseDownloads(data));

    // 1. Update Hero Version Pill
    const versionPill = document.querySelector('.version-pill');
    if (versionPill) {
      versionPill.textContent = `${data.tag_name} • Native GTK4 & Libadwaita`;
    }

    // 3. Auto-populate Latest Release Card Title & Metadata
    const titleEl = document.getElementById('latestReleaseTitle');
    if (titleEl) {
      titleEl.textContent = `Glyph ${data.name || data.tag_name}`;
    }

    const badgeEl = document.getElementById('latestReleaseBadge');
    if (badgeEl) {
      badgeEl.textContent = 'Latest';
    }

    const ghLinkEl = document.getElementById('latestReleaseGitHubLink');
    if (ghLinkEl && data.html_url) {
      ghLinkEl.href = data.html_url;
    }

    const subtitleEl = document.getElementById('latestReleaseSubtitle');
    if (subtitleEl && data.published_at) {
      try {
        const pubDate = new Date(data.published_at).toLocaleDateString('en-US', {
          month: 'long',
          day: 'numeric',
          year: 'numeric'
        });
        subtitleEl.textContent = `Latest Release • Published on ${pubDate}`;
      } catch {
        subtitleEl.textContent = 'Latest Release';
      }
    }

    // 4. Parse & Auto-populate Release Notes Body
    const bodyEl = document.getElementById('latestReleaseBody');
    if (bodyEl && data.body) {
      const parsedHtml = parseReleaseMarkdown(data.body);
      if (parsedHtml) {
        bodyEl.innerHTML = parsedHtml;
      }
    }
  } catch {
    renderReleaseDownloads(null);
  } finally {
    clearTimeout(timeout);
  }
}

function parseReleaseMarkdown(md) {
  function escapeHtml(text) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function inlineFormat(text) {
    return escapeHtml(text)
      .replace(/\*\*([^*]+)\*\*:\s*/g, '<strong>$1</strong> - ')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  const lines = md.split(/\r?\n/);
  let html = '';
  let inList = false;
  let currentLi = '';

  function flushLi() {
    if (currentLi) {
      html += `<li>${currentLi}</li>`;
      currentLi = '';
    }
  }

  function closeList() {
    flushLi();
    if (inList) {
      html += '</ul>';
      inList = false;
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    // Skip installation tables, horizontal rules, and changelog footer links
    if (line.startsWith('---') || line.startsWith('|') || line.includes('Installation Quick Links') || line.includes('Full Changelog')) {
      closeList();
      break;
    }

    // Headings
    if (line.startsWith('## ') || line.startsWith('### ')) {
      closeList();
      const headingText = escapeHtml(line.replace(/^#{2,3}\s+/, ''));
      if (headingText.toLowerCase().includes("what's changed")) continue;
      html += `<h4 class="release-subheading">${headingText}</h4>`;
      continue;
    }

    // Bullet items
    if (line.startsWith('* ') || line.startsWith('- ')) {
      if (!inList) {
        html += '<ul class="release-notes-list">';
        inList = true;
      } else {
        flushLi();
      }
      currentLi = inlineFormat(line.substring(2).trim());
      continue;
    }

    // Multiline continuation of previous bullet
    if (inList) {
      currentLi += ' ' + inlineFormat(line);
      continue;
    }

    // Paragraph
    closeList();
    html += `<p class="release-paragraph">${inlineFormat(line)}</p>`;
  }

  closeList();
  return html;
}

