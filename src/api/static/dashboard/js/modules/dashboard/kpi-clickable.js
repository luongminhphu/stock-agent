/**
 * kpi-clickable.js
 * Owner: modules/dashboard
 * Responsibility: make KPI summary cards clickable — click scrolls to
 * the relevant section and optionally triggers a filter change.
 *
 * Exports:
 *   initKpiClickable() — no deps, pure DOM.
 */

const KPI_MAP = [
  {
    cardId:   'riskyTheses',
    targetId: 'thesisBoardTitle',
    label:    'Xem thesis rủi ro',
    onEnter:  () => {
      const filter = document.getElementById('statusFilter');
      if (filter && filter.value !== 'active') {
        filter.value = 'active';
        filter.dispatchEvent(new Event('change'));
      }
    },
  },
  {
    cardId:   'staleReviewCard',
    targetId: 'thesesTableWrap',
    label:    'Xem thesis cần review',
  },
  {
    cardId:   'upcoming7d',
    targetId: 'catalystList',
    label:    'Xem catalyst calendar',
  },
  {
    cardId:   'openTheses',
    targetId: 'thesisBoardTitle',
    label:    'Xem thesis board',
  },
];

function scrollToId(targetId, offset = 0) {
  const el = document.getElementById(targetId);
  if (!el) return;
  const y = el.getBoundingClientRect().top + window.scrollY - offset;
  window.scrollTo({ top: y, behavior: 'smooth' });
}

export function initKpiClickable() {
  KPI_MAP.forEach(({ cardId, targetId, label, onEnter }) => {
    const card = document.getElementById(cardId)
      ?? document.querySelector(`[id="${cardId}"]`);
    if (!card) return;

    const article = card.tagName === 'ARTICLE' ? card : card.closest('article') ?? card;

    article.classList.add('kpi--clickable');
    article.setAttribute('role', 'button');
    article.setAttribute('tabindex', '0');
    article.setAttribute('aria-label', label);

    const handle = () => {
      onEnter?.();
      scrollToId(targetId, 72);
    };

    article.addEventListener('click', handle);
    article.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handle(); }
    });
  });
}
