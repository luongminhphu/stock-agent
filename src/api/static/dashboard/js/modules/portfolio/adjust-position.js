/**
 * adjust-position.js — modal điều chỉnh vị thế theo cổ tức cổ phiếu / chia tách (ES module)
 * Owner: modules/portfolio
 *
 * Investor problem: sau ngày chốt quyền chia cổ tức bằng cổ phiếu (hoặc split),
 * số lượng cp trong tài khoản tăng nhưng giá vốn TB danh nghĩa giảm. Nếu không
 * điều chỉnh Position trong hệ thống: unrealized P&L sai, sizing sai, stop-breach
 * check sai (giá tham chiếu đã điều chỉnh xuống nhưng stop/avg_cost vẫn giá cũ).
 *
 * Backend: POST /api/v1/portfolio/adjust { ticker, ratio, reason, note }
 *   ratio 0.15 = cổ tức 15% (1,000 → 1,150 cp)
 *   ratio 1.0  = split 1:2 (1,000 → 2,000 cp)
 *   Cost-preserving: qty × (1+ratio), avg_cost ÷ (1+ratio) — tổng vốn không đổi.
 *
 * Public API:
 *   init()                            — call once after DOM ready
 *   injectAdjustButtons(tbody, opts)  — thêm nút [±] cạnh [B] [S]
 *   openAdjustModal(ticker, opts)     — opts: { currentQty, currentAvg }
 *
 * Events dispatched on success:
 *   'trade:confirmed' — để app.js refresh holdings + attention (cùng hook với quick-trade)
 */

const MODAL_ID = 'adj-modal';

// ---------------------------------------------------------------------------
// Modal HTML (injected once into <body>)
// ---------------------------------------------------------------------------
function _ensureModal() {
  if (document.getElementById(MODAL_ID)) return;
  const wrapper = document.createElement('div');
  wrapper.innerHTML = `
    <div id="${MODAL_ID}" class="qt-backdrop" role="dialog" aria-modal="true" aria-labelledby="adj-title" hidden>
      <div class="qt-modal">
        <div class="qt-modal-header">
          <span id="adj-title" class="qt-modal-title">Điều chỉnh vị thế</span>
          <button class="qt-close" id="adj-close-btn" aria-label="Đóng">✕</button>
        </div>
        <div class="qt-modal-body">
          <div class="qt-ticker-row">
            <span class="qt-badge qt-badge-adjust">±</span>
            <span class="qt-ticker-label" id="adj-ticker-display"></span>
          </div>

          <div id="adj-current" class="adj-current" aria-live="polite"></div>

          <label class="qt-label">Loại sự kiện</label>
          <div class="adj-reason-row">
            <label class="adj-radio">
              <input type="radio" name="adj-reason" value="stock_dividend" checked />
              Cổ tức bằng cổ phiếu
            </label>
            <label class="adj-radio">
              <input type="radio" name="adj-reason" value="split" />
              Chia tách cổ phiếu
            </label>
          </div>

          <label class="qt-label" for="adj-ratio">Tỷ lệ thưởng/tách (%)</label>
          <input class="qt-input" id="adj-ratio" type="number" min="1" max="1000" step="1"
            placeholder="VD: 15 (thưởng 15%) · 100 (split 1:2)" />
          <div class="adj-ratio-hint" id="adj-ratio-hint">
            15 = nhận thêm 15 cp cho mỗi 100 cp đang giữ · 100 = split 1:2
          </div>

          <!-- Edit mode: sửa trực tiếp qty/avg — chỉ hiện khi _currentMode='edit' -->
          <div id="adj-edit-fields" hidden>
            <label class="qt-label" for="adj-new-qty">Số lượng mới (cp)</label>
            <input class="qt-input" id="adj-new-qty" type="number" min="0" step="100"
              placeholder="VD: 1500" />
            <label class="qt-label" for="adj-new-avg">Giá vốn TB mới (₫)</label>
            <input class="qt-input" id="adj-new-avg" type="number" min="0" step="100"
              placeholder="VD: 23500" />
            <div class="adj-ratio-hint">
              Sửa thẳng, không audit trail — dùng để sync với tài khoản thật.
              Để trống ô nào nếu không muốn đổi.
            </div>
          </div>

          <label class="qt-label" for="adj-note">Ghi chú (tuỳ chọn)</label>
          <input class="qt-input" id="adj-note" type="text" maxlength="500"
            placeholder="VD: Cổ tức 2025, chốt quyền 01/08" />

          <!-- History mode: timeline trades của ticker — chỉ hiện khi mode='history' -->
          <div id="adj-history-list" hidden></div>

          <div class="qt-summary" id="adj-summary"></div>
          <div class="qt-error" id="adj-error" hidden></div>
        </div>
        <div class="qt-modal-footer">
          <button class="qt-btn qt-btn-secondary" id="adj-cancel-btn">Huỷ</button>
          <button class="qt-btn qt-btn-primary" id="adj-confirm-btn">Xác nhận</button>
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
let _currentAvg    = 0;
let _currentMode   = 'adjust';   // 'adjust' | 'edit' | 'history'

// ---------------------------------------------------------------------------
// Open / close
// ---------------------------------------------------------------------------
export function openAdjustModal(ticker, opts) {
  _currentTicker = ticker.toUpperCase();
  _currentQty    = Number(opts?.currentQty) || 0;
  _currentAvg    = Number(opts?.currentAvg) || 0;
  _currentMode   = ['edit', 'history'].includes(opts?.mode) ? opts.mode : 'adjust';

  const isEdit    = _currentMode === 'edit';
  const isHistory = _currentMode === 'history';
  document.getElementById('adj-ticker-display').textContent = _currentTicker;
  document.getElementById('adj-title').textContent =
    (isEdit ? 'Sửa trực tiếp vị thế — ' : isHistory ? 'Lịch sử vị thế — ' : 'Điều chỉnh vị thế — ')
    + _currentTicker;

  // Toggle adjust-only vs edit-only field groups
  const reasonRow = document.querySelector('.adj-reason-row');
  const ratioIn   = document.getElementById('adj-ratio');
  const ratioLbl  = ratioIn.previousElementSibling;   // <label> "Tỷ lệ..."
  const ratioHint = document.getElementById('adj-ratio-hint');
  const noteLbl   = document.getElementById('adj-note').previousElementSibling;
  const noteIn    = document.getElementById('adj-note');
  const currentRow = document.getElementById('adj-current');
  const summaryEl  = document.getElementById('adj-summary');
  [reasonRow, ratioLbl, ratioIn, ratioHint, noteLbl, noteIn]
    .forEach(el => { if (el) el.hidden = isEdit || isHistory; });
  document.getElementById('adj-edit-fields').hidden  = !isEdit;
  document.getElementById('adj-history-list').hidden = !isHistory;
  if (currentRow) currentRow.hidden = isHistory;
  if (summaryEl)  summaryEl.hidden  = isHistory;
  // Footer: history mode chỉ cần nút Đóng
  const confirmBtn = document.getElementById('adj-confirm-btn');
  const cancelBtn  = document.getElementById('adj-cancel-btn');
  if (confirmBtn) confirmBtn.hidden = isHistory;
  if (cancelBtn)  cancelBtn.textContent = isHistory ? 'Đóng' : 'Huỷ';

  document.getElementById('adj-ratio').value = '';
  document.getElementById('adj-note').value  = '';
  document.getElementById('adj-new-qty').value = '';
  document.getElementById('adj-new-avg').value = '';
  document.querySelector('input[name="adj-reason"][value="stock_dividend"]').checked = true;
  _hideError();
  _renderPreview();
  if (isHistory) {
    _loadHistory();
  } else {
    setTimeout(() => document.getElementById(isEdit ? 'adj-new-qty' : 'adj-ratio').focus(), 0);
  }

  document.getElementById(MODAL_ID).removeAttribute('hidden');
  document.getElementById('adj-ratio').focus();
}

function _closeModal() {
  document.getElementById(MODAL_ID)?.setAttribute('hidden', '');
}

// ---------------------------------------------------------------------------
// Preview — tính trước qty/avg mới để investor kiểm tra trước khi confirm
// ---------------------------------------------------------------------------
function _renderPreview() {
  const currentEl = document.getElementById('adj-current');
  const summaryEl = document.getElementById('adj-summary');

  const qtyStr = _currentQty > 0 ? _currentQty.toLocaleString('vi-VN') : '—';
  const avgStr = _currentAvg > 0 ? _currentAvg.toLocaleString('vi-VN') : '—';
  currentEl.innerHTML =
    `Hiện tại: <strong>${qtyStr} cp</strong> · giá vốn TB <strong>${avgStr} ₫</strong>`;

  if (_currentMode === 'edit') {
    const newQty = parseFloat(document.getElementById('adj-new-qty').value);
    const newAvg = parseFloat(document.getElementById('adj-new-avg').value);
    if ((!newQty || newQty <= 0) && (!newAvg || newAvg <= 0)) { summaryEl.textContent = ''; return; }
    const effQty = (newQty && newQty > 0) ? newQty : _currentQty;
    const effAvg = (newAvg && newAvg > 0) ? newAvg : _currentAvg;
    const deltaVon = effQty * effAvg - _currentQty * _currentAvg;
    const sign = deltaVon >= 0 ? '+' : '−';
    summaryEl.innerHTML =
      `Sau sửa: <strong>${effQty.toLocaleString('vi-VN')} cp</strong> @ ` +
      `<strong>${effAvg.toLocaleString('vi-VN')} ₫</strong> · tổng vốn ` +
      `${sign}${Math.abs(deltaVon).toLocaleString('vi-VN', { maximumFractionDigits: 0 })} ₫`;
    return;
  }

  const ratioPct  = parseFloat(document.getElementById('adj-ratio').value);

  const qtyStr = _currentQty > 0 ? _currentQty.toLocaleString('vi-VN') : '—';
  const avgStr = _currentAvg > 0 ? _currentAvg.toLocaleString('vi-VN') : '—';
  if (!ratioPct || ratioPct <= 0 || _currentQty <= 0) {
    summaryEl.textContent = '';
    return;
  }
  const ratio  = ratioPct / 100;
  const newQty = _currentQty * (1 + ratio);
  const bonus  = _currentQty * ratio;
  const newAvg = _currentAvg > 0 ? _currentAvg / (1 + ratio) : 0;
  summaryEl.innerHTML =
    `Sau điều chỉnh: <strong>${newQty.toLocaleString('vi-VN', { maximumFractionDigits: 0 })} cp</strong> ` +
    `(+${bonus.toLocaleString('vi-VN', { maximumFractionDigits: 0 })}) · ` +
    `giá vốn TB ≈ <strong>${newAvg.toLocaleString('vi-VN', { maximumFractionDigits: 0 })} ₫</strong> · ` +
    `tổng vốn không đổi`;
}

// ---------------------------------------------------------------------------
// Submit
// ---------------------------------------------------------------------------
async function _handleConfirm() {
  _hideError();
  if (_currentMode === 'edit') return _handleEditConfirm();
  const ratioPct = parseFloat(document.getElementById('adj-ratio').value);
  const reason   = document.querySelector('input[name="adj-reason"]:checked')?.value || 'stock_dividend';
  const note     = document.getElementById('adj-note').value.trim() || null;

  if (!ratioPct || ratioPct <= 0) { _showError('Tỷ lệ phải lớn hơn 0. VD: 15 nghĩa là thưởng 15%.'); return; }
  if (ratioPct > 1000)            { _showError('Tỷ lệ quá lớn (>1000%). Kiểm tra lại.'); return; }

  const btn = document.getElementById('adj-confirm-btn');
  btn.disabled    = true;
  btn.textContent = 'Đang xử lý…';

  try {
    const res = await fetch('/api/v1/portfolio/adjust', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ ticker: _currentTicker, ratio: ratioPct / 100, reason, note }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      _showError(data?.detail ?? `Lỗi ${res.status}`);
      return;
    }
    const result = await res.json();
    _closeModal();
    _showToast(
      `✅ Đã điều chỉnh ${result.ticker}: ` +
      `${result.old_qty.toLocaleString('vi-VN')} → ${result.new_qty.toLocaleString('vi-VN', { maximumFractionDigits: 0 })} cp · ` +
      `giá vốn ${result.old_avg_cost.toLocaleString('vi-VN', { maximumFractionDigits: 0 })} → ` +
      `${result.new_avg_cost.toLocaleString('vi-VN', { maximumFractionDigits: 0 })} ₫`,
    );

    if (typeof window.__qtRefreshHoldings === 'function') window.__qtRefreshHoldings();
    document.dispatchEvent(new CustomEvent('trade:confirmed', {
      detail: { ticker: result.ticker, trade_type: 'adjust' },
    }));
  } catch {
    _showError('Không thể kết nối server. Kiểm tra lại kết nối.');
  } finally {
    btn.disabled    = false;
    btn.textContent = 'Xác nhận';
  }
}

async function _handleEditConfirm() {
  const qtyRaw = document.getElementById('adj-new-qty').value.trim();
  const avgRaw = document.getElementById('adj-new-avg').value.trim();
  const qty = qtyRaw ? parseFloat(qtyRaw) : null;
  const avg = avgRaw ? parseFloat(avgRaw) : null;

  if (qty === null && avg === null) { _showError('Nhập ít nhất một giá trị mới (số lượng hoặc giá vốn).'); return; }
  if (qty !== null && (isNaN(qty) || qty <= 0)) { _showError('Số lượng mới phải lớn hơn 0.'); return; }
  if (avg !== null && (isNaN(avg) || avg <= 0)) { _showError('Giá vốn mới phải lớn hơn 0.'); return; }

  const btn = document.getElementById('adj-confirm-btn');
  btn.disabled    = true;
  btn.textContent = 'Đang xử lý…';

  try {
    const payload = {};
    if (qty !== null) payload.qty = qty;
    if (avg !== null) payload.avg_cost = avg;
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
      `✏️ Đã sửa ${result.ticker}: ${result.qty.toLocaleString('vi-VN')} cp @ ` +
      `${result.avg_cost.toLocaleString('vi-VN', { maximumFractionDigits: 0 })} ₫`,
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
// History mode — GET /portfolio/trades?ticker=…
// ---------------------------------------------------------------------------
const _HIST_META = {
  buy:    { icon: '🟢', label: 'MUA',    cls: 'hist-buy'    },
  sell:   { icon: '🔴', label: 'BÁN',    cls: 'hist-sell'   },
  adjust: { icon: '⚖️', label: 'ĐIỀU CHỈNH', cls: 'hist-adjust' },
};

async function _loadHistory() {
  const listEl = document.getElementById('adj-history-list');
  listEl.innerHTML = '<div class="hist-loading">Đang tải…</div>';
  try {
    const res = await fetch(`/api/v1/portfolio/trades?ticker=${encodeURIComponent(_currentTicker)}&limit=50`);
    if (!res.ok) {
      listEl.innerHTML = `<div class="hist-empty">Lỗi tải lịch sử (${res.status})</div>`;
      return;
    }
    const data = await res.json();
    if (!data.items?.length) {
      listEl.innerHTML = '<div class="hist-empty">Chưa có giao dịch nào cho mã này.</div>';
      return;
    }
    listEl.innerHTML = data.items.map(t => {
      const meta = _HIST_META[t.trade_type] || { icon: '•', label: t.trade_type.toUpperCase(), cls: '' };
      const when = t.traded_at ? new Date(t.traded_at).toLocaleString('vi-VN', {
        day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
      }) : '—';
      const qtyStr  = t.qty.toLocaleString('vi-VN');
      const mainLine = t.trade_type === 'adjust'
        ? `+${qtyStr} cp`
        : `${qtyStr} cp @ ${t.price.toLocaleString('vi-VN')} ₫`;
      const pnl = (t.trade_type === 'sell' && t.realized_pnl != null)
        ? `<span class="hist-pnl ${t.realized_pnl >= 0 ? 'positive' : 'negative'}">P&L ${t.realized_pnl >= 0 ? '+' : ''}${t.realized_pnl.toLocaleString('vi-VN')} ₫</span>`
        : '';
      const note = t.note ? `<div class="hist-note">${t.note}</div>` : '';
      return `<div class="hist-item ${meta.cls}">
        <div class="hist-row">
          <span class="hist-icon">${meta.icon}</span>
          <span class="hist-label">${meta.label}</span>
          <span class="hist-main">${mainLine}</span>
          ${pnl}
          <span class="hist-when">${when}</span>
        </div>
        ${note}
      </div>`;
    }).join('');
  } catch {
    listEl.innerHTML = '<div class="hist-empty">Không thể kết nối server.</div>';
  }
}

// ---------------------------------------------------------------------------
// Events / helpers
// ---------------------------------------------------------------------------
function _bindModalEvents() {
  document.getElementById('adj-close-btn').addEventListener('click', _closeModal);
  document.getElementById('adj-cancel-btn').addEventListener('click', _closeModal);
  document.getElementById('adj-confirm-btn').addEventListener('click', _handleConfirm);
  document.getElementById('adj-ratio').addEventListener('input', _renderPreview);
  document.getElementById('adj-new-qty').addEventListener('input', _renderPreview);
  document.getElementById('adj-new-avg').addEventListener('input', _renderPreview);
  document.getElementById(MODAL_ID).addEventListener('click', e => {
    if (e.target.id === MODAL_ID) _closeModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !document.getElementById(MODAL_ID).hidden) _closeModal();
  });
}

function _showError(msg) {
  const el = document.getElementById('adj-error');
  el.textContent = msg;
  el.hidden = false;
}

function _hideError() {
  const el = document.getElementById('adj-error');
  el.hidden = true;
}

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
 * Thêm nút [±] vào các .action-btns đã có sẵn (do quick-trade injectTradeButtons tạo).
 * Idempotent. Đọc current qty/avg từ data attributes của row (nếu renderer cung cấp)
 * hoặc fetch từ portfolio loader cache.
 *
 * @param {HTMLElement} tbody
 */
export function injectAdjustButtons(tbody) {
  if (!tbody) return;
  tbody.querySelectorAll('tr[data-ticker]').forEach(row => {
    const wrap = row.querySelector('.action-btns');
    if (!wrap || wrap.querySelector('.adj-btn-inline')) return; // idempotent

    const btn = document.createElement('button');
    btn.className   = 'qt-btn-inline adj-btn-inline';
    btn.textContent = '±';
    btn.title       = 'Điều chỉnh theo cổ tức/split';
    btn.setAttribute('aria-label', `Điều chỉnh ${row.dataset.ticker}`);
    btn.addEventListener('click', e => {
      e.stopPropagation();
      openAdjustModal(row.dataset.ticker, {
        currentQty: parseFloat(row.dataset.qty) || 0,
        currentAvg: parseFloat(row.dataset.avgCost) || 0,
      });
    });
    wrap.appendChild(btn);

    // Nút ✏️ sửa trực tiếp qty/giá vốn (PUT /positions/{ticker})
    if (!wrap.querySelector('.edit-btn-inline')) {
      const editBtn = document.createElement('button');
      editBtn.className   = 'qt-btn-inline edit-btn-inline';
      editBtn.textContent = '\u270e';
      editBtn.title       = 'Sửa trực tiếp số lượng / giá vốn';
      editBtn.setAttribute('aria-label', `Sửa trực tiếp ${row.dataset.ticker}`);
      editBtn.addEventListener('click', e => {
        e.stopPropagation();
        openAdjustModal(row.dataset.ticker, {
          currentQty: parseFloat(row.dataset.qty) || 0,
          currentAvg: parseFloat(row.dataset.avgCost) || 0,
          mode: 'edit',
        });
      });
      wrap.appendChild(editBtn);
    }

    // Nút ≡ xem lịch sử thay đổi vị thế (GET /portfolio/trades)
    if (!wrap.querySelector('.hist-btn-inline')) {
      const histBtn = document.createElement('button');
      histBtn.className   = 'qt-btn-inline hist-btn-inline';
      histBtn.textContent = '\u2261';
      histBtn.title       = 'Lịch sử thay đổi vị thế';
      histBtn.setAttribute('aria-label', `Lịch sử ${row.dataset.ticker}`);
      histBtn.addEventListener('click', e => {
        e.stopPropagation();
        openAdjustModal(row.dataset.ticker, {
          currentQty: parseFloat(row.dataset.qty) || 0,
          currentAvg: parseFloat(row.dataset.avgCost) || 0,
          mode: 'history',
        });
      });
      wrap.appendChild(histBtn);
    }
  });
}
