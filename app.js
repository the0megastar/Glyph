// Glyph Website Interactive Logic

document.addEventListener('DOMContentLoaded', () => {
  initThemeToggle();
  initScreenshotTabs();
  initCopyButton();
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

  // Initial load
  applyTheme(getPreferredTheme());

  themeToggleBtn.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
  });

  // Listen for system preference changes if no manual preference stored
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
    if (!localStorage.getItem('glyph-theme')) {
      applyTheme(e.matches ? 'light' : 'dark');
    }
  });
}

/* 2. Screenshot Showcase Tab Switcher */
function initScreenshotTabs() {
  const tabs = document.querySelectorAll('.tab-btn');
  const imgLibrary = document.getElementById('img-library');
  const imgDetail = document.getElementById('img-detail');
  const windowTitle = document.getElementById('windowTitle');

  if (!tabs.length || !imgLibrary || !imgDetail) return;

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const target = tab.getAttribute('data-target');

      tabs.forEach((t) => {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
      });

      tab.classList.add('active');
      tab.setAttribute('aria-selected', 'true');

      if (target === 'detail') {
        imgLibrary.classList.add('hidden');
        imgDetail.classList.remove('hidden');
        if (windowTitle) windowTitle.textContent = 'Glyph — Manga & Comic Reader';
      } else {
        imgDetail.classList.add('hidden');
        imgLibrary.classList.remove('hidden');
        if (windowTitle) windowTitle.textContent = 'Glyph — Comic Library';
      }
    });
  });
}

/* 3. Command Box Copy Functionality */
function initCopyButton() {
  const copyBtn = document.getElementById('copyBtn');
  const commandText = document.getElementById('commandText');
  const copyBtnLabel = document.getElementById('copyBtnLabel');

  if (!copyBtn || !commandText) return;

  copyBtn.addEventListener('click', async () => {
    const textToCopy = commandText.textContent.trim();

    try {
      await navigator.clipboard.writeText(textToCopy);
      copyBtn.classList.add('copied');
      if (copyBtnLabel) copyBtnLabel.textContent = 'Copied!';

      setTimeout(() => {
        copyBtn.classList.remove('copied');
        if (copyBtnLabel) copyBtnLabel.textContent = 'Copy';
      }, 2200);
    } catch (err) {
      // Fallback for older browsers
      const textarea = document.createElement('textarea');
      textarea.value = textToCopy;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      try {
        document.execCommand('copy');
        copyBtn.classList.add('copied');
        if (copyBtnLabel) copyBtnLabel.textContent = 'Copied!';
        setTimeout(() => {
          copyBtn.classList.remove('copied');
          if (copyBtnLabel) copyBtnLabel.textContent = 'Copy';
        }, 2200);
      } finally {
        document.body.removeChild(textarea);
      }
    }
  });
}

/* 4. Progressive Enhancement: Latest GitHub Release Check */
async function fetchLatestRelease() {
  try {
    const res = await fetch('https://api.github.com/repos/the0megastar/Glyph/releases/latest');
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.tag_name) {
      const versionPill = document.querySelector('.version-pill');
      if (versionPill) {
        versionPill.innerHTML = `<span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#33d17a;"></span> ${data.tag_name} • Native GTK4 & Libadwaita`;
      }
    }
  } catch {
    // Graceful silent fallback
  }
}
