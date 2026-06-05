/**
 * brief-ticker.js
 * Owner: modules/briefing
 * Responsibility: wire [data-brief-ticker] chip click/keydown → loadThesisDetail + scroll.
 *
 * Exports:
 *   bindBriefTickerClick(deps) — deps: { state, loadThesisDetail }
 */

/**
 * @param {{ state: { theses: Array<{ id: number, ticker?: string }> }, loadThesisDetail: (id: number) => void }} deps
 */
export function bindBriefTickerClick({ state, loadThesisDetail }) {
  document.addEventListener('click', e => {
    const chip = e.target.closest('[data-brief-ticker]');
    if (!chip) return;
    const ticker = chip.dataset.briefTicker?.toUpperCase();
    if (!ticker) return;
    const thesis = state.theses.find(t => t.ticker?.toUpperCase() === ticker);
    if (!thesis) return;
    loadThesisDetail(thesis.id);
    document.getElementById('thesesTableWrap')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const chip = e.target.closest('[data-brief-ticker]');
    if (!chip) return;
    e.preventDefault();
    chip.click();
  });
}
