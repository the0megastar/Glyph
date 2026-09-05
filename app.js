// Glyph Website Interactive Logic

document.addEventListener('DOMContentLoaded', () => {
  initThemeToggle();
  initCopyButtons();
  initDownloadDropdown();
  fetchLatestRelease();
});

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
    dropdownMenu.classList.remove('open');
    splitGroup.classList.remove('dropdown-open');
    toggleBtn.setAttribute('aria-expanded', 'false');
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
  try {
    const res = await fetch('https://api.github.com/repos/the0megastar/Glyph/releases/latest');
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.tag_name) return;

    // 1. Update Hero Version Pill
    const versionPill = document.querySelector('.version-pill');
    if (versionPill) {
      versionPill.textContent = `${data.tag_name} • Native GTK4 & Libadwaita`;
    }

    // 2. Update Download Dropdown and Primary Download URLs
    if (Array.isArray(data.assets)) {
      data.assets.forEach(asset => {
        const url = asset.browser_download_url;
        const name = asset.name.toLowerCase();

        if (name.includes('x86_64.flatpak')) {
          const el = document.getElementById('downloadFlatpakX86');
          if (el) el.href = url;
          const primary = document.getElementById('primaryDownloadBtn');
          if (primary) primary.href = url;
        } else if (name.includes('aarch64.flatpak')) {
          const el = document.getElementById('downloadFlatpakArm');
          if (el) el.href = url;
        } else if (name.endsWith('.appimage')) {
          const el = document.getElementById('downloadAppImage');
          if (el) el.href = url;
        } else if (name.endsWith('.deb')) {
          const el = document.getElementById('downloadDeb');
          if (el) el.href = url;
        } else if (name.endsWith('.rpm')) {
          const el = document.getElementById('downloadRpm');
          if (el) el.href = url;
        }
      });
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
    // Graceful silent fallback keeps pre-rendered HTML in place
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

