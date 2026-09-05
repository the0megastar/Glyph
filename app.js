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

/* 5. Progressive Enhancement: Latest GitHub Release Check */
async function fetchLatestRelease() {
  try {
    const res = await fetch('https://api.github.com/repos/the0megastar/Glyph/releases/latest');
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.tag_name) {
      const versionPill = document.querySelector('.version-pill');
      if (versionPill) {
        versionPill.textContent = `${data.tag_name} • Native GTK4 & Libadwaita`;
      }
    }
  } catch {
    // Graceful silent fallback
  }
}
