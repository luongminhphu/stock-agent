/**
 * quick-trade.js — B/S quick-trade modal (ES module)
 * Owner: modules/portfolio
 * Moved from: js/quick-trade.js (IIFE → ES module)
 *
 * Public API:
 *   init()                          — call once after DOM ready
 *   injectTradeButtons(tbody, opts) — call after each table re-render
 *   openModal(ticker, type, thesisId, opts)
 *
 * Thesis wiring modes (controlled by opts.fromThesisTab):
 *   false (Trades tab) — renders <select> dropdown with active theses for the ticker.
 *   true  (Thesis tab) — thesis_id already known; shows read-only badge, no dropdown.
 *
 * Events dispatched on success:
 *   'trade:confirmed'   — always; lets app.js refresh watchlist + AttentionPanel.
 *   'decision:logged'   — only when backend confirms decision_logged: true.
 *
 * Backward compat:
 *   window.__qtRefreshHoldings hook is still respected so portfolio-loader.js
 *   does not need to change.
 */

// ---------------------------------------------------------------------------
// Modal HTML (injected once into <body>)
// ---------------------------------------------------------------------------
const MODAL_ID = 'qt-modal';

function _ensureModal() {
  if (document.getElementById(MODAL_ID)) return;
  const wrapper = document.createElement('div');
  wrapper.innerHTML = `
    <div id="${MODAL_ID}" class="qt-backdrop" role="dialog" aria-modal="true" aria-labelledby="qt-title" hidden>
      <div class="qt-modal">
        <div class="qt-modal-header">
          <span id="qt-title" class="qt-modal-title">Lệnh giao dịch</span>
          <button class="qt-close" id="qt-close-btn" aria-label="Đóng">✕</button>
        </div>
        <div class="qt-modal-body">
          <div class="qt-ticker-row">
            <span class="qt-badge" id="qt-type-badge">MUA</span>
            <span class="qt-ticker-label" id="qt-ticker-display"></span>
          </div>

          <label class="qt-label" for="qt-qty">Số lượng (cp)</label>
          <input class="qt-input" id="qt-qty" type="number" min="1" step="100" placeholder="VD: 1000" />

          <label class="qt-label" for="qt-price">Giá (VND/cp)</label>
          <input class="qt-input" id="qt-price" type="number" min="100" step="100" placeholder="VD: 48500" />

          <div id="qt-thesis-section">
            <div id="qt-thesis-dropdown-wrap">
              <label class="qt-label" for="qt-thesis-select">
                Thesis liên kết <span class="qt-optional">(tùy chọn — để log decision)</span>
              </label>
              <select class="qt-input qt-select" id="qt-thesis-select">
                <option value="">— Không liên kết thesis —</option>
              </select>
            </div>
            <div id="qt-thesis-badge-wrap" hidden>
              <label class="qt-label">Thesis</label>
              <div class="qt-thesis-readonly" id="qt-thesis-badge-label"></div>
            </div>
          </div>

          <label class="qt-label" id="qt-rationale-label" for="qt-rationale">
            Lý do quyết định <span class="qt-optional" id="qt-rationale-hint">(tuỳ chọn)</span>
          </label>
          <textarea class="qt-input qt-textarea" id="qt-rationale" maxlength="500"
            placeholder="VD: Breakout khỏi vùng tích luũ, volume tăng mạnh" rows="3"></textarea>

          <label class="qt-label" for="qt-note">Ghi chú (tuỳ chọn)</label>
          <input class="qt-input" id="qt-note" type="text" maxlength="200" placeholder="" />

          <div class="qt-summary" id="qt-summary"></div>
          <div class="qt-error" id="qt-error" hidden></div>
        </div>
        <div class="qt-modal-footer">
          <button class="qt-btn qt-btn-secondary" id="qt-cancel-btn">Huỷ</button>
          <button class="qt-btn qt-btn-primary" id="qt-confirm-btn">Xác nhận</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(wrapper.firstElementChild);
  _bindModalEvents();
}

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------
let _currentTicker   = '';
let _currentType     = 'buy';
let _currentThesisId = null;
let _fromThesisTab   = false;

// ---------------------------------------------------------------------------
// Rationale hint
// ---------------------------------------------------------------------------
function _updateRationaleHint(thesisSelected) {
  const hint = document.getElementById('qt-rationale-hint');
  if (!hint) return;
  if (thesisSelected) {
    hint.textContent   = '— điền để log decision ✓';
    hint.style.color      = 'var(--color-primary, #01696f)';
    hint.style.fontWeight = '500';
  } else {
    hint.textContent      = '(tuỳ chọn)';
    hint.style.color      = '';
    hint.style.fontWeight = '';
  }
}

// ---------------------------------------------------------------------------
// Modal open / close
// ---------------------------------------------------------------------------
export function openModal(ticker, type, thesisId, opts) {
  _currentTicker   = ticker.toUpperCase();
  _currentType     = type;
  _currentThesisId = thesisId || null;
  _fromThesisTab   = !!(opts && opts.fromThesisTab);

  const badge = document.getElementById('qt-type-badge');
  badge.textContent = type === 'buy' ? 'MUA' : 'BÁN';
  badge.className   = 'qt-badge ' + (type === 'buy' ? 'qt-badge-buy' : 'qt-badge-sell');

  document.getElementById('qt-ticker-display').textContent = _currentTicker;
  document.getElementById('qt-title').textContent =
    (type === 'buy' ? 'Lệnh MUA — ' : 'Lệnh BÁN — ') + _currentTicker;
  document.getElementById('qt-qty').value       = '';
  document.getElementById('qt-price').value     = '';
  document.getElementById('qt-rationale').value = '';
  document.getElementById('qt-note').value      = '';
  document.getElementById('qt-summary').textContent = '';
  _hideError();

  const dropdownWrap = document.getElementById('qt-thesis-dropdown-wrap');
  const badgeWrap    = document.getElementById('qt-thesis-badge-wrap');
  const badgeLabel   = document.getElementById('qt-thesis-badge-label');

  if (_fromThesisTab) {
    dropdownWrap.hidden = true;
    badgeWrap.hidden    = false;
    badgeLabel.textContent = _currentThesisId ? `Thesis #${_currentThesisId}` : '—';
    _updateRationaleHint(!!_currentThesisId);
  } else {
    dropdownWrap.hidden = false;
    badgeWrap.hidden    = true;
    _updateRationaleHint(!!_currentThesisId);
    _loadThesisOptions(_currentTicker, _currentThesisId);
  }

  document.getElementById(MODAL_ID).removeAttribute('hidden');
  document.getElementById('qt-qty').focus();
}

function _closeModal() {
  document.getElementById(MODAL_ID)?.setAttribute('hidden', '');
}

// ---------------------------------------------------------------------------
// Thesis dropdown (Trades tab only)
// ---------------------------------------------------------------------------
async function _loadThesisOptions(ticker, preselectedId) {
  const select = document.getElementById('qt-thesis-select');
  if (!select) return;
  select.innerHTML = '<option value="">Đang tải thesis…</option>';
  select.disabled  = true;
  try {
    const res = await fetch(
      `/api/v1/readmodel/dashboard/theses?status=active&ticker=${encodeURIComponent(ticker)}&enrich_prices=false`,
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data  = await res.json();
    const items = (data.items || []).filter(t => t.ticker === ticker);
    select.innerHTML = '<option value="">— Không liên kết thesis —</option>';
    items.forEach(t => {
      const opt       = document.createElement('option');
      opt.value       = String(t.id);
      const verdict   = t.last_verdict ? ` (${t.last_verdict})` : '';
      opt.textContent = `#${t.id} ${t.ticker} — ${t.title || 'Không có tiêu đề'}${verdict}`;
      if (preselectedId && t.id === preselectedId) opt.selected = true;
      select.appendChild(opt);
    });
    if (items.length === 1 && !preselectedId) select.value = String(items[0].id);
    _updateRationaleHint(!!select.value);
  } catch {
    select.innerHTML = '<option value="">— Không tải được thesis —</option>';
    _updateRationaleHint(false);
  } finally {
    select.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Modal events
// ---------------------------------------------------------------------------
function _bindModalEvents() {
  document.getElementById('qt-close-btn').addEventListener('click',  _closeModal);
  document.getElementById('qt-cancel-btn').addEventListener('click', _closeModal);
  document.getElementById(MODAL_ID).addEventListener('click', function (e) {
    if (e.target === this) _closeModal();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') _closeModal(); });
  ['qt-qty', 'qt-price'].forEach(id =>
    document.getElementById(id).addEventListener('input', _updateSummary),
  );
  document.getElementById('qt-thesis-select').addEventListener('change', function () {
    _updateRationaleHint(!!this.value);
  });
  document.getElementById('qt-confirm-btn').addEventListener('click', _handleConfirm);
}

function _updateSummary() {
  const qty   = parseFloat(document.getElementById('qt-qty').value)   || 0;
  const price = parseFloat(document.getElementById('qt-price').value) || 0;
  const el    = document.getElementById('qt-summary');
  el.textContent = (qty > 0 && price > 0)
    ? `Tổng giá trị: ${(qty * price).toLocaleString('vi-VN')} ₫  (${qty.toLocaleString('vi-VN')} cp × ${price.toLocaleString('vi-VN')} ₫)`
    : '';
}

function _showError(msg) {
  const el = document.getElementById('qt-error');
  el.textContent = msg;
  el.removeAttribute('hidden');
}

function _hideError() {
  document.getElementById('qt-error')?.setAttribute('hidden', '');
}

function _resolveThesisId() {
  if (_fromThesisTab) return _currentThesisId;
  const select = document.getElementById('qt-thesis-select');
  if (select?.value) return parseInt(select.value, 10);
  return _currentThesisId;
}

// ---------------------------------------------------------------------------
// Submit
// ---------------------------------------------------------------------------
async function _handleConfirm() {
  _hideError();
  const qty       = parseFloat(document.getElementById('qt-qty').value);
  const price     = parseFloat(document.getElementById('qt-price').value);
  const rationale = document.getElementById('qt-rationale').value.trim() || null;
  const note      = document.getElementById('qt-note').value.trim()      || null;

  if (!qty   || qty   <= 0) { _showError('Số lượng phải lớn hơn 0.'); return; }
  if (!price || price <= 0) { _showError('Giá phải lớn hơn 0.');    return; }

  const btn = document.getElementById('qt-confirm-btn');
  btn.disabled    = true;
  btn.textContent = 'Đang xử lý…';

  const resolvedThesisId = _resolveThesisId();
  const body = { ticker: _currentTicker, qty, price };
  if (note)             body.note      = note;
  if (rationale)        body.rationale = rationale;
  if (resolvedThesisId) body.thesis_id = resolvedThesisId;

  try {
    const res = await fetch(`/api/v1/portfolio/${_currentType}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      _showError(data?.detail ?? `Lỗi ${res.status}`);
      return;
    }
    const result = await res.json();
    _closeModal();
    _showToast(_buildSuccessMsg(result));

    // Refresh holdings (backward compat hook)
    if (typeof window.__qtRefreshHoldings === 'function') window.__qtRefreshHoldings();

    if (result.decision_logged) {
      document.dispatchEvent(new CustomEvent('decision:logged', {
        detail: { ticker: result.ticker, trade_type: result.trade_type, price: result.price, qty: result.qty },
      }));
    }
    document.dispatchEvent(new CustomEvent('trade:confirmed', {
      detail: { ticker: result.ticker, trade_type: result.trade_type },
    }));
  } catch {
    _showError('Không thể kết nối server. Kiểm tra lại kết nối.');
  } finally {
    btn.disabled    = false;
    btn.textContent = 'Xác nhận';
  }
}

function _buildSuccessMsg(result) {
  const pnl = result.realized_pnl != null
    ? ` | Realized P&L: ${result.realized_pnl >= 0 ? '+' : ''}${result.realized_pnl.toLocaleString('vi-VN')} ₫`
    : '';
  const closed   = result.position_closed  ? ' | Vị thế đã đóng'       : '';
  const decision = result.decision_logged  ? ' | 📋 Decision logged' : '';
  return (result.trade_type === 'buy' ? '✅ Đã mua ' : '✅ Đã bán ')
    + `${result.qty.toLocaleString('vi-VN')} cp ${result.ticker} @ ${result.price.toLocaleString('vi-VN')} ₫`
    + pnl + closed + decision;
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
function _showToast(msg) {
  let container = document.getElementById('qt-toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'qt-toast-container';
    container.style.cssText =
      'position:fixed;bottom:1.5rem;right:1.5rem;z-index:9999;' +
      'display:flex;flex-direction:column;gap:.5rem;max-width:420px;pointer-events:none';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.textContent = msg;
  toast.style.cssText =
    'background:var(--color-surface,#fff);color:var(--color-text,#111);' +
    'border:1px solid var(--color-border,#ddd);border-radius:8px;' +
    'padding:.75rem 1rem;font-size:.875rem;box-shadow:0 4px 16px rgba(0,0,0,.1);' +
    'pointer-events:auto;opacity:0;transform:translateY(8px);' +
    'transition:opacity .2s,transform .2s';
  container.appendChild(toast);
  requestAnimationFrame(() => { toast.style.opacity = '1'; toast.style.transform = 'translateY(0)'; });
  setTimeout(() => {
    toast.style.opacity = '0'; toast.style.transform = 'translateY(8px)';
    setTimeout(() => toast.remove(), 250);
  }, 4000);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
export function init() {
  _ensureModal();
}

/**
 * Inject [B] [S] buttons into .col-action cells of each tbody row.
 * Idempotent — skips rows that already have .action-btns.
 *
 * @param {HTMLElement} tbody
 * @param {{ fromThesisTab?: boolean }} [opts]
 */
export function injectTradeButtons(tbody, opts) {
  if (!tbody) return;
  const fromThesisTab = !!(opts?.fromThesisTab);

  tbody.querySelectorAll('tr[data-ticker]').forEach(row => {
    const ticker   = row.dataset.ticker;
    const thesisId = row.dataset.thesisId ? parseInt(row.dataset.thesisId, 10) : null;
    if (!ticker) return;

    const actionCell = row.querySelector('td.col-action');
    const targetCell = actionCell || row.querySelector('td:first-child');
    if (!targetCell) return;
    if (targetCell.querySelector('.action-btns')) return; // idempotent

    if (actionCell) actionCell.innerHTML = '';

    const wrap = document.createElement('div');
    wrap.className = 'action-btns';

    const make = (type, label, ariaLabel) => {
      const btn = document.createElement('button');
      btn.className   = `qt-btn-inline qt-btn-${type}`;
      btn.textContent = label;
      btn.setAttribute('aria-label', `${ariaLabel} ${ticker}`);
      btn.addEventListener('click', e => {
        e.stopPropagation();
        openModal(ticker, type, thesisId, { fromThesisTab });
      });
      return btn;
    };

    wrap.appendChild(make('buy',  'B', 'Mua'));
    wrap.appendChild(make('sell', 'S', 'Bán'));

    if (actionCell) actionCell.appendChild(wrap);
    else targetCell.prepend(wrap);
  });
}
