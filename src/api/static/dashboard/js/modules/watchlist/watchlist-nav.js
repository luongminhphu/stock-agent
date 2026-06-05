/**
 * watchlist-nav.js
 * Owner: modules/watchlist
 * Responsibility:
 *   - bindWatchlistThesisNavigate: listen navigate:thesis → loadThesisDetail + scroll.
 *   - bindWatchlistAddModal: wire #watchlistAddForm submit → handleAddTicker.
 *
 * Exports:
 *   bindWatchlistThesisNavigate(deps)
 *   bindWatchlistAddModal(deps)
 */

/**
 * @param {{ loadThesisDetail: (id: number) => void }} deps
 */
export function bindWatchlistThesisNavigate({ loadThesisDetail }) {
  document.addEventListener('navigate:thesis', (e) => {
    const { thesisId } = e.detail ?? {};
    if (!thesisId) return;
    loadThesisDetail(thesisId);
    document.getElementById('thesesTableWrap')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

/**
 * @param {{ closeModal: (id: string) => void, handleAddTicker: (ticker: string, note: string) => Promise<void> }} deps
 */
export function bindWatchlistAddModal({ closeModal, handleAddTicker }) {
  const form = document.getElementById('watchlistAddForm');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const ticker = form.querySelector('#watchlistTickerInput')?.value?.trim();
    const note   = form.querySelector('#watchlistNoteInput')?.value?.trim() ?? '';
    if (!ticker) return;
    closeModal('watchlistAddModal');
    form.reset();
    await handleAddTicker(ticker, note);
  });
}
