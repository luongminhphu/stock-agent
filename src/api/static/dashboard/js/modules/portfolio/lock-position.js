/**
 * lock-position.js — modal đánh dấu vị thế bị khóa bán (ES module)
 * Owner: modules/portfolio (UI adapter mỏng — domain rule nằm ở src/portfolio)
 *
 * Investor problem: cp ESOP / phát hành thêm đang trong thời gian hạn chế
 * chuyển nhượng không bán được. Nếu hệ thống không biết phần khóa: sizing
 * gợi ý bán quá phần bán được, stop-breach embed báo "exit_signal" trong
 * khi thực tế không thoát được. Đánh dấu khóa cho phép toàn hệ thống
 * (dashboard tag, quick-trade warning, stop-breach embed) phản ánh đúng.
 *
 * Backend: PUT /api/v1/portfolio/positions/{ticker}
 *   { locked_qty, locked_reason, locked_until } — locked_qty = 0 = mở khóa.
 *
 * Public API:
 *   init()                              — call once after DOM ready
 *   openLockModal(ticker, opts)         — opts: { qty, lockedQty, lockedReason, lockedUntil }
 *
 * Events dispatched on success:
 *   'trade:confirmed' — cùng hook với quick-trade/adjust để refresh holdings
 */

const MODAL_ID = 'lock-modal';

const REASONS = [
  ['esop',               'ESOP'],
  ['private_placement',  'Phát hành riêng lẻ'],
  ['pending_settlement', 'CP chờ về (T+)'],
  ['pledged',            'Cầm cố / ký quỹ margin'],
  ['odd_lot',            'Lô lẻ'],
  ['core_hold',          'Nắm giữ lõi (không chủ động bán)'],
];

function _fmt(n) {
  return Number(n).toLocaleString('vi-VN');
}

function _isoToVi(iso) {
  return iso ? String(iso).split('-').reverse().join('/') : null;
}

// ---------------------------------------------------------------------------
// Modal HTML (injected once into <body>) — reuse qt-modal chrome
// ---------------------------------------------------------------------------
function _ensureModal() {
  if (document.getElementById(MODAL_ID)) return;
  const wrapper = document.createElement('div');
  wrapper.innerHTML = `
    <div id="${MODAL_ID}" class="qt-backdrop" role="dialog" aria-modal="true" aria-labelledby="lock-title" hidden>
      <div class="qt-modal">
        <div class="qt-modal-header">
          <span id="lock-title" class="qt-modal-title">Đánh dấu khóa bán</span>
          <button class="qt-close" id="lock-close-btn" aria-label="Đóng">✕</button>
        </div>
        <div class="qt-modal-body">
          <div class="qt-ticker-row">
            <span class="qt-badge qt-badge-adjust">\u{1F512}</span>
            <span class="qt-ticker-label" id="lock-ticker-display"></span>
          </div>

          <div id="lock-current" class="adj-current" aria-live="polite"></div>

          <label class="qt-label" for="lock-qty">Số cp bị khóa (không bán được)</label>
          <input class="qt-input" id="lock-qty" type="number" min="0" step="100"
            placeholder="VD: 5000 — nhập 0 để mở khóa toàn bộ" />

          <div id="lock-fields">
            <label class="qt-label" for="lock-reason">Lý do khóa</label>
            <select class="qt-input" id="lock-reason">
              ${REASONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('\n              ')}
            </select>

            <label class="qt-label" for="lock-until">Ngày dự kiến mở khóa</label>
            <input class="qt-input" id="lock-until" type="date" />
            <div class="adj-ratio-hint">
              Để trống nếu chưa rõ — vẫn nhận diện khóa, chỉ thiếu mốc hẹn review.
            </div>
          </div>

          <div class="qt-summary" id="lock-summary"></div>
          <div class="qt-error" id="lock-error" hidden></div>
        </div>
        <div class="qt-modal-footer">
          <button class="qt-btn qt-btn-secondary" id="lock-cancel-btn">Huỷ</button>
          <button class="qt-btn qt-btn-primary" id="lock-confirm-btn">Xác nhận</button>
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
let _currentTicker = '';
let _currentQty    = 0;

// ---------------------------------------------------------------------------
// Open / close
// ---------------------------------------------------------------------------
export function openLockModal(ticker, opts) {
  _currentTicker = ticker.toUpperCase();
  _currentQty    = Number(opts?.qty) || 0;

  const lockedQty    = Number(opts?.lockedQty) || 0;
  const lockedReason = opts?.lockedReason || '';
  const lockedUntil  = opts?.lockedUntil || '';

  document.getElementById('lock-ticker-display').textContent = _currentTicker;
  document.getElementById('lock-title').textContent =
    (lockedQty > 0 ? 'Cập nhật khóa bán — ' : 'Đánh dấu khóa bán — ') + _currentTicker;

  const currentEl = document.getElementById('lock-current');
  if (lockedQty > 0) {
    const rTxt = (REASONS.find(([v]) => v === lockedReason) || [null, lockedReason])[1];
    const dTxt = _isoToVi(lockedUntil);
    currentEl.innerHTML =
      `Đang khóa: <strong>${_fmt(lockedQty)} cp</strong> (${rTxt || 'hạn chế chuyển nhượng'}` +
      `${dTxt ? `, mở khóa ${dTxt}` : ''}) · tổng sở hữu <strong>${_fmt(_currentQty)} cp</strong>`;
  } else {
    currentEl.innerHTML = `Đang sở hữu: <strong>${_fmt(_currentQty)} cp</strong> — chưa có phần khóa`;
  }

  document.getElementById('lock-qty').value   = lockedQty > 0 ? lockedQty : '';
  document.getElementById('lock-reason').value =
    REASONS.some(([v]) => v === lockedReason) ? lockedReason : 'esop';
  document.getElementById('lock-until').value = lockedUntil || '';

  _hideError();
  _renderSummary();
  document.getElementById(MODAL_ID).removeAttribute('hidden');
  document.getElementById('lock-qty').focus();
}

function _closeModal() {
  document.getElementById(MODAL_ID)?.setAttribute('hidden', '');
}

// ---------------------------------------------------------------------------
// Summary — preview phần bán được trước khi confirm
// ---------------------------------------------------------------------------
function _renderSummary() {
  const summaryEl = document.getElementById('lock-summary');
  const fieldsEl  = document.getElementById('lock-fields');
  const qtyEl     = document.getElementById('lock-qty');
  const locked    = parseFloat(qtyEl.value);

  if (qtyEl.value === '' || isNaN(locked)) { summaryEl.textContent = ''; fieldsEl.hidden = false; return; }
  if (locked < 0) { summaryEl.innerHTML = '<span class="cell-error">Số khóa không được âm.</span>'; fieldsEl.hidden = false; return; }
  if (_currentQty > 0 && locked > _currentQty) {
    summaryEl.innerHTML = `<span class="cell-error">Số khóa (${_fmt(locked)}) lớn hơn tổng sở hữu (${_fmt(_currentQty)}).</span>`;
    fieldsEl.hidden = false;
    return;
  }

  if (locked === 0) {
    fieldsEl.hidden = true;   // mở khóa toàn bộ — không cần reason/until
    summaryEl.innerHTML = 'Mở khóa toàn bộ: <strong>' + _fmt(_currentQty) + ' cp</strong> đều bán được.';
    return;
  }

  fieldsEl.hidden = false;
  const sellable = _currentQty > 0 ? _currentQty - locked : null;
  summaryEl.innerHTML =
    `Sau khóa: <strong>${_fmt(locked)} cp</strong> không bán được` +
    (sellable != null ? ` · bán được tối đa <strong>${_fmt(sellable)} cp</strong>` : '');
}

// ---------------------------------------------------------------------------
// Submit — PUT /api/v1/portfolio/positions/{ticker}
// ---------------------------------------------------------------------------
async function _handleConfirm() {
  _hideError();
  const raw    = document.getElementById('lock-qty').value.trim();
  const locked = raw === '' ? null : parseFloat(raw);

  if (locked === null || isNaN(locked)) { _showError('Nhập số cp bị khóa (0 = mở khóa toàn bộ).'); return; }
  if (locked < 0)                       { _showError('Số khóa phải >= 0.'); return; }
  if (_currentQty > 0 && locked > _currentQty) {
    _showError(`Số khóa (${_fmt(locked)}) vượt tổng sở hữu (${_fmt(_currentQty)}).`);
    return;
  }

  const payload = { locked_qty: locked };
  if (locked > 0) {
    payload.locked_reason = document.getElementById('lock-reason').value;
    const until = document.getElementById('lock-until').value;
    if (until) payload.locked_until = until;
  }

  const btn = document.getElementById('lock-confirm-btn');
  btn.disabled    = true;
  btn.textContent = 'Đang xử lý…';

  try {
    const res = await fetch(`/api/v1/portfolio/positions/${encodeURIComponent(_currentTicker)}`, {
      method:  'PUT',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      _showError(data?.detail ?? `Lỗi ${res.status}`);
      return;
    }
    const result = await res.json();
    _closeModal();
    _showToast(
      result.locked_qty > 0
        ? `${result.ticker}: khóa ${result.locked_qty.toLocaleString('vi-VN')} cp — còn bán được ${(result.qty - result.locked_qty).toLocaleString('vi-VN')} cp`
        : `${result.ticker}: đã mở khóa toàn bộ`,
    );

    if (typeof window.__qtRefreshHoldings === 'function') window.__qtRefreshHoldings();
    document.dispatchEvent(new CustomEvent('trade:confirmed', {
      detail: { ticker: result.ticker, trade_type: 'edit' },
    }));
  } catch {
    _showError('Không thể kết nối server. Kiểm tra lại kết nối.');
  } finally {
    btn.disabled    = false;
    btn.textContent = 'Xác nhận';
  }
}

// ---------------------------------------------------------------------------
// Events / helpers
// ---------------------------------------------------------------------------
function _bindModalEvents() {
  document.getElementById('lock-close-btn').addEventListener('click', _closeModal);
  document.getElementById('lock-cancel-btn').addEventListener('click', _closeModal);
  document.getElementById('lock-confirm-btn').addEventListener('click', _handleConfirm);
  document.getElementById('lock-qty').addEventListener('input', _renderSummary);
  document.getElementById(MODAL_ID).addEventListener('click', e => {
    if (e.target.id === MODAL_ID) _closeModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !document.getElementById(MODAL_ID).hidden) _closeModal();
  });
}

function _showError(msg) {
  const el = document.getElementById('lock-error');
  el.textContent = msg;
  el.hidden = false;
}

function _hideError() {
  document.getElementById('lock-error').hidden = true;
}

// HSC: toast nền elevated, text trắng, radius 2px — không emoji
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
    'background:var(--hsc-n80,#4D545C);color:#FFFFFF;' +
    'border:1px solid var(--hsc-neutral-a24,rgba(214,231,255,.24));border-radius:var(--hsc-radius-tag,2px);' +
    'padding:.75rem 1rem;font-size:.875rem;' +
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
