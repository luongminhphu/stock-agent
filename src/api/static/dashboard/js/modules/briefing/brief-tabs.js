/**
 * brief-tabs.js
 * Owner: modules/briefing (UI concern)
 * Responsibility:
 *   - bindBriefTabs: wire tab-bar click → show/hide brief panes.
 *   - initBriefAutoOpen: auto-expand briefing collapsible and activate
 *     the correct tab based on current VN time (GMT+7).
 *
 * Time windows:
 *   morning  06:00–10:59 ICT → activate morning tab
 *   eod      14:30–18:30 ICT → activate eod tab
 *
 * Rules:
 *   - No fetch, no API calls — pure DOM.
 *   - Guard every getElementById / querySelector — no throw on missing el.
 */

/**
 * Wire .brief-tab-bar click → show/hide .brief-tab-pane.
 * Uses aria-controls to resolve the target pane id.
 */
export function bindBriefTabs() {
  const tabBar = document.querySelector('.brief-tab-bar');
  if (!tabBar) return;

  tabBar.addEventListener('click', e => {
    const btn = e.target.closest('.brief-tab');
    if (!btn) return;

    const targetId = btn.getAttribute('aria-controls');
    if (!targetId) return;

    tabBar.querySelectorAll('.brief-tab').forEach(t => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
    document.querySelectorAll('.brief-tab-pane').forEach(p => {
      p.classList.add('hidden');
    });

    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');
    document.getElementById(targetId)?.classList.remove('hidden');
  });
}

/**
 * Auto-open #briefCollapsible and activate the correct tab
 * if current VN time falls within morning (06:00–10:59) or
 * eod (14:30–18:30) windows.
 */
export function initBriefAutoOpen() {
  const collapsible = document.getElementById('briefCollapsible');
  if (!collapsible) return;

  const now    = new Date();
  const vnHour = (now.getUTCHours() + 7) % 24;
  const vnMin  = now.getUTCMinutes();
  const vnTime = vnHour + vnMin / 60;

  const isMorningWindow = vnTime >= 6 && vnTime < 11;
  const isEodWindow     = vnTime >= 14.5 && vnTime <= 18.5;

  if (!isMorningWindow && !isEodWindow) return;

  collapsible.open = true;

  const targetTab = isMorningWindow ? 'morning' : 'eod';
  const tabBar    = collapsible.querySelector('.brief-tab-bar');
  if (!tabBar) return;

  tabBar.querySelectorAll('.brief-tab').forEach(t => {
    const isTarget = t.dataset.tab === targetTab;
    t.classList.toggle('active', isTarget);
    t.setAttribute('aria-selected', String(isTarget));
  });

  const morningPane = document.getElementById('morningBriefWrap');
  const eodPane     = document.getElementById('eodBriefWrap');
  if (isMorningWindow) {
    morningPane?.classList.remove('hidden');
    eodPane?.classList.add('hidden');
  } else {
    eodPane?.classList.remove('hidden');
    morningPane?.classList.add('hidden');
  }
}
