/**
 * mobile-nav.js — Bottom Tab Bar navigation controller
 *
 * Responsibilities:
 *  - On ≤900px: show only the active cluster, hide others
 *  - Sync active state on bottom tab buttons
 *  - Restore scroll position per cluster (UX polish)
 *  - Respond to resize: re-show all clusters on desktop
 *
 * Usage: imported and init'd in app.js after DOM ready.
 */

const MOBILE_BREAKPOINT = 900;

const CLUSTERS = [
  { id: 'cluster-a', label: 'Hôm nay',  icon: `<svg viewBox="0 0 24 24"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>` },
  { id: 'cluster-b', label: 'Thesis',   icon: `<svg viewBox="0 0 24 24"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>` },
  { id: 'cluster-c', label: 'Quyết định', icon: `<svg viewBox="0 0 24 24"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>` },
  { id: 'cluster-d', label: 'Học được', icon: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>` },
  { id: 'cluster-e', label: 'Vòng quay', icon: `<svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>` },
];

let _activeCluster = 'cluster-a';
const _scrollPositions = {};

// ── Helpers ──────────────────────────────────────────────────────────────────

function isMobile() {
  return window.innerWidth <= MOBILE_BREAKPOINT;
}

function applyMobileState() {
  if (!isMobile()) {
    // Desktop: restore all clusters visible
    CLUSTERS.forEach(({ id }) => {
      const el = document.getElementById(id);
      if (el) el.classList.remove('mobile-active');
      // On desktop clusters show via CSS (display: block default)
    });
    return;
  }

  // Mobile: show only active cluster
  CLUSTERS.forEach(({ id }) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (id === _activeCluster) {
      el.classList.add('mobile-active');
    } else {
      el.classList.remove('mobile-active');
    }
  });
}

function syncTabBar() {
  const tabs = document.querySelectorAll('.mobile-tab');
  tabs.forEach(tab => {
    const target = tab.dataset.cluster;
    tab.classList.toggle('active', target === _activeCluster);
    tab.setAttribute('aria-selected', target === _activeCluster ? 'true' : 'false');
  });
}

function switchCluster(clusterId) {
  if (!CLUSTERS.find(c => c.id === clusterId)) return;

  // Save scroll position of outgoing cluster
  _scrollPositions[_activeCluster] = window.scrollY;

  _activeCluster = clusterId;
  applyMobileState();
  syncTabBar();

  // Restore scroll position or scroll to top
  const savedY = _scrollPositions[clusterId] ?? 0;
  window.scrollTo({ top: savedY, behavior: 'instant' });
}

// ── Build DOM ────────────────────────────────────────────────────────────────

function buildTabBar() {
  if (document.getElementById('mobileTabBar')) return; // already built

  const bar = document.createElement('nav');
  bar.id = 'mobileTabBar';
  bar.className = 'mobile-tab-bar';
  bar.setAttribute('role', 'tablist');
  bar.setAttribute('aria-label', 'Cluster navigation');

  CLUSTERS.forEach(({ id, label, icon }) => {
    const btn = document.createElement('button');
    btn.className = 'mobile-tab';
    btn.dataset.cluster = id;
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', id === _activeCluster ? 'true' : 'false');
    btn.setAttribute('aria-controls', id);
    btn.setAttribute('type', 'button');
    btn.innerHTML = `
      <span class="mobile-tab__icon">${icon}</span>
      <span class="mobile-tab__label">${label}</span>
    `;
    btn.addEventListener('click', () => switchCluster(id));
    bar.appendChild(btn);
  });

  document.body.appendChild(bar);
}

// ── Init ─────────────────────────────────────────────────────────────────────

export function initMobileNav() {
  buildTabBar();

  // Set initial state
  if (isMobile()) {
    applyMobileState();
    syncTabBar();
  }

  // Re-evaluate on resize
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (isMobile()) {
        applyMobileState();
        syncTabBar();
      } else {
        // Desktop: remove mobile-active so CSS can show all
        CLUSTERS.forEach(({ id }) => {
          const el = document.getElementById(id);
          if (el) el.classList.remove('mobile-active');
        });
      }
    }, 120);
  });
}

// Active cluster getter (for external use)
export function getActiveCluster() {
  return _activeCluster;
}
