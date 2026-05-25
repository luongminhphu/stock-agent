/**
 * quick-trade.js — B/S quick-trade buttons for Holdings table
 *
 * Injects a [B] and [S] button into the .col-action cell of every holdings row.
 * Clicking opens a modal → user enters qty + price + optional rationale
 * → POST /api/v1/portfolio/buy|sell
 *
 * Thesis wiring — two modes controlled by `fromThesisTab` flag:
 *
 *   fromThesisTab = false  (Trades tab)
 *     - Renders a <select> dropdown populated with active theses for the ticker.
 *     - If position already has a linked thesis_id (data-thesis-id on <tr>),
 *       that option is pre-selected.
 *     - If only one thesis exists for the ticker, it is auto-selected.
 *     - User can change the selection or leave it blank (no DecisionLog).
 *
 *   fromThesisTab = true  (Thesis tab)
 *     - thesis_id is already known from the row — no dropdown needed.
 *     - Modal shows a read-only thesis badge instead of a dropdown.
 *     - thesis_id is always forwarded to the backend.
 *
 * Decision log hint:
 *   When thesis is selected, the rationale label updates in realtime to signal
 *   that a DecisionLog will be created if the user fills in the rationale.
 *   Rationale remains optional — thesis + rationale → decision logged;
 *   thesis without rationale → trade succeeds, decision silently skipped.
 *
 * On success:
 *   - Dismisses modal, refreshes holdings data, shows inline toast.
 *   - If backend confirms decision_logged: true, dispatches CustomEvent
 *     'decision:logged' on document so Cluster C auto-refreshes without
 *     requiring manual input.
 *
 * Dependencies: none (vanilla JS, no framework).
 * Public API:
 *   QuickTrade.init()                    — call once after DOM is ready
 *   QuickTrade.injectTradeButtons(tbody) — call after each table re-render
 *   QuickTrade.openModal(ticker, type, thesisId, opts)
 */

(function (global) {
  'use strict';

  // ─── Modal HTML (injected once into <body>) ─────────────────────────────────────────────
  const MODAL_ID = 'qt-modal';

  function ensureModal() {
    if (document.getElementById(MODAL_ID)) return;
    const el = document.createElement('div');
    el.innerHTML = `
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

            <!-- Thesis section — toggled by fromThesisTab -->
            <div id="qt-thesis-section">
              <!-- Trades tab: dropdown -->
              <div id="qt-thesis-dropdown-wrap">
                <label class="qt-label" for="qt-thesis-select">
                  Thesis liên kết <span class="qt-optional">(tùy chọn — để log decision)</span>
                </label>
                <select class="qt-input qt-select" id="qt-thesis-select">
                  <option value="">— Không liên kết thesis —</option>
                </select>
              </div>
              <!-- Thesis tab: read-only badge -->
              <div id="qt-thesis-badge-wrap" hidden>
                <label class="qt-label">Thesis</label>
                <div class="qt-thesis-readonly" id="qt-thesis-badge-label"></div>
              </div>
            </div>

            <label class="qt-label" id="qt-rationale-label" for="qt-rationale">Lý do quyết định <span class="qt-optional" id="qt-rationale-hint">(tuỳ chọn)</span></label>
            <textarea class="qt-input qt-textarea" id="qt-rationale" maxlength="500"
              placeholder="VD: Breakout khỏi vùng tích luĩ, volume tăng mạnh" rows="3"></textarea>

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
    document.body.appendChild(el.firstElementChild);
    _bindModalEvents();
  }

  // ─── State ────────────────────────────────────────────────────────────────────────────
  let _currentTicker      = '';
  let _currentType        = 'buy';
  let _currentThesisId    = null;   // resolved thesis_id to send to backend
  let _fromThesisTab      = false;  // true → suppress dropdown, show badge

  // ─── Rationale hint ─────────────────────────────────────────────────────────────────
  /**
   * Cập nhật hint label của rationale dựa trên trạng thái thesis.
   * Được gọi mỗi khi: modal mở, user thay đổi thesis dropdown.
   */
  function _updateRationaleHint(thesisSelected) {
    const hintEl = document.getElementById('qt-rationale-hint');
    if (!hintEl) return;
    if (thesisSelected) {
      hintEl.textContent = '— điền để log decision ✓';
      hintEl.style.color = 'var(--color-primary, #01696f)';
      hintEl.style.fontWeight = '500';
    } else {
      hintEl.textContent = '(tuỳ chọn)';
      hintEl.style.color = '';
      hintEl.style.fontWeight = '';
    }
  }

  // ─── Modal lifecycle ──────────────────────────────────────────────────────────────
  /**
   * @param {string} ticker
   * @param {'buy'|'sell'} type
   * @param {number|null} thesisId  — from data-thesis-id on <tr>
   * @param {{ fromThesisTab?: boolean }} opts
   */
  function openModal(ticker, type, thesisId, opts) {
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
    const select       = document.getElementById('qt-thesis-select');

    if (_fromThesisTab) {
      // Thesis tab — thesis already known, show read-only badge
      dropdownWrap.hidden = true;
      badgeWrap.hidden    = false;
      badgeLabel.textContent = _currentThesisId
        ? `Thesis #${_currentThesisId}`
        : '—';
      // Thesis tab: thesis_id is always set → hint active immediately
      _updateRationaleHint(!!_currentThesisId);
    } else {
      // Trades tab — show dropdown, populate async
      dropdownWrap.hidden = false;
      badgeWrap.hidden    = true;
      // Hint will be updated after dropdown loads (via change listener + initial state)
      _updateRationaleHint(!!_currentThesisId);
      _loadThesisOptions(_currentTicker, _currentThesisId);
    }

    document.getElementById(MODAL_ID).removeAttribute('hidden');
    document.getElementById('qt-qty').focus();
  }

  function closeModal() {
    document.getElementById(MODAL_ID).setAttribute('hidden', '');
  }

  // ─── Thesis dropdown population (Trades tab only) ───────────────────────────────────
  async function _loadThesisOptions(ticker, preselectedId) {
    const select = document.getElementById('qt-thesis-select');
    if (!select) return;

    // Reset + loading state
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

      items.forEach(function (t) {
        const opt = document.createElement('option');
        opt.value       = String(t.id);
        const verdict   = t.last_verdict ? ` (${t.last_verdict})` : '';
        opt.textContent = `#${t.id} ${t.ticker} — ${t.title || 'Không có tiêu đề'}${verdict}`;
        if (preselectedId && t.id === preselectedId) opt.selected = true;
        select.appendChild(opt);
      });

      // Auto-select if exactly one thesis for this ticker
      if (items.length === 1 && !preselectedId) {
        select.value = String(items[0].id);
      }

      // Update hint after options loaded (auto-select may have set a value)
      _updateRationaleHint(!!select.value);
    } catch (_err) {
      // Fail silently — user can still trade without thesis link
      select.innerHTML = '<option value="">— Không tải được thesis —</option>';
      _updateRationaleHint(false);
    } finally {
      select.disabled = false;
    }
  }

  // ─── Events ────────────────────────────────────────────────────────────────────────────
  function _bindModalEvents() {
    document.getElementById('qt-close-btn').addEventListener('click', closeModal);
    document.getElementById('qt-cancel-btn').addEventListener('click', closeModal);
    document.getElementById(MODAL_ID).addEventListener('click', function (e) {
      if (e.target === this) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeModal();
    });
    ['qt-qty', 'qt-price'].forEach(function (id) {
      document.getElementById(id).addEventListener('input', _updateSummary);
    });

    // Realtime rationale hint: update when thesis dropdown changes
    document.getElementById('qt-thesis-select').addEventListener('change', function () {
      _updateRationaleHint(!!this.value);
    });

    document.getElementById('qt-confirm-btn').addEventListener('click', _handleConfirm);
  }

  function _updateSummary() {
    const qty   = parseFloat(document.getElementById('qt-qty').value)   || 0;
    const price = parseFloat(document.getElementById('qt-price').value) || 0;
    const el    = document.getElementById('qt-summary');
    if (qty > 0 && price > 0) {
      const total = qty * price;
      el.textContent =
        'Tổng giá trị: ' + total.toLocaleString('vi-VN') + ' ₫  (' +
        qty.toLocaleString('vi-VN') + ' cp × ' +
        price.toLocaleString('vi-VN') + ' ₫)';
    } else {
      el.textContent = '';
    }
  }

  function _showError(msg) {
    const el = document.getElementById('qt-error');
    el.textContent = msg;
    el.removeAttribute('hidden');
  }

  function _hideError() {
    document.getElementById('qt-error').setAttribute('hidden', '');
  }

  // ─── Resolve final thesis_id before submit ──────────────────────────────────────────
  function _resolveThesisId() {
    if (_fromThesisTab) {
      // Thesis tab: always use the row's thesis_id
      return _currentThesisId;
    }
    // Trades tab: prefer dropdown selection over row's thesis_id
    const select = document.getElementById('qt-thesis-select');
    if (select && select.value) return parseInt(select.value, 10);
    return _currentThesisId;
  }

  // ─── API call ────────────────────────────────────────────────────────────────────────────
  async function _handleConfirm() {
    _hideError();
    const qty       = parseFloat(document.getElementById('qt-qty').value);
    const price     = parseFloat(document.getElementById('qt-price').value);
    const rationale = document.getElementById('qt-rationale').value.trim() || null;
    const note      = document.getElementById('qt-note').value.trim() || null;

    if (!qty || qty <= 0)     { _showError('Số lượng phải lớn hơn 0.'); return; }
    if (!price || price <= 0) { _showError('Giá phải lớn hơn 0.'); return; }

    const btn = document.getElementById('qt-confirm-btn');
    btn.disabled    = true;
    btn.textContent = 'Đang xử lý…';

    const resolvedThesisId = _resolveThesisId();
    const endpoint = '/api/v1/portfolio/' + _currentType;
    const body = { ticker: _currentTicker, qty, price };
    if (note)               body.note      = note;
    if (rationale)          body.rationale = rationale;
    if (resolvedThesisId)   body.thesis_id = resolvedThesisId;

    try {
      const res = await fetch(endpoint, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msg  = (data && data.detail) ? data.detail : `Lỗi ${res.status}`;
        _showError(msg);
        return;
      }

      const result = await res.json();
      closeModal();
      _showToast(_buildSuccessMsg(result));

      // Refresh holdings
      if (typeof global.__qtRefreshHoldings === 'function') {
        global.__qtRefreshHoldings();
      }

      // ─── Loop wire: trade action → Cluster C auto-refresh ───────────────────
      // If backend confirmed decision was logged (thesis_id + rationale
      // were provided), notify the app so Cluster C refreshes automatically
      // without any manual input. Loose coupling via CustomEvent — this
      // module knows nothing about decision-loader.js.
      if (result.decision_logged) {
        document.dispatchEvent(new CustomEvent('decision:logged', {
          detail: {
            ticker:     result.ticker,
            trade_type: result.trade_type,  // 'buy' | 'sell'
            price:      result.price,
            qty:        result.qty,
          },
        }));
      }
    } catch (err) {
      _showError('Không thể kết nối server. Kiểm tra lại kết nối.');
    } finally {
      btn.disabled    = false;
      btn.textContent = 'Xác nhận';
    }
  }

  function _buildSuccessMsg(result) {
    const sign = result.realized_pnl != null
      ? (result.realized_pnl >= 0 ? '+' : '') + result.realized_pnl.toLocaleString('vi-VN') + ' ₫'
      : null;
    let msg = (result.trade_type === 'buy' ? '✅ Đã mua ' : '✅ Đã bán ') +
      result.qty.toLocaleString('vi-VN') + ' cp ' + result.ticker +
      ' @ ' + result.price.toLocaleString('vi-VN') + ' ₫';
    if (sign) msg += ' | Realized P&L: ' + sign;
    if (result.position_closed) msg += ' | Vị thế đã đóng';
    if (result.decision_logged) msg += ' | 📋 Decision logged';
    return msg;
  }

  // ─── Toast ────────────────────────────────────────────────────────────────────────────
  function _showToast(msg) {
    let container = document.getElementById('qt-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'qt-toast-container';
      document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className   = 'qt-toast';
    toast.textContent = msg;
    container.appendChild(toast);
    requestAnimationFrame(() => { toast.classList.add('qt-toast-visible'); });
    setTimeout(() => {
      toast.classList.remove('qt-toast-visible');
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  // ─── Button injection ──────────────────────────────────────────────────────────────
  /**
   * Inject [B] [S] buttons vào ô .col-action của mỗi row trong tbody.
   *
   * @param {HTMLElement} tbodyEl
   * @param {{ fromThesisTab?: boolean }} opts
   *   fromThesisTab = true  → Thesis tab: thesis_id đã chắc, không cần dropdown
   *   fromThesisTab = false → Trades tab: dropdown fetch theo ticker
   */
  function injectTradeButtons(tbodyEl, opts) {
    if (!tbodyEl) return;
    const fromThesisTab = !!(opts && opts.fromThesisTab);

    tbodyEl.querySelectorAll('tr').forEach(function (row) {
      const actionCell = row.querySelector('td.col-action');
      const targetCell = actionCell || row.querySelector('td:first-child');
      if (!targetCell) return;

      if (targetCell.querySelector('.qt-btn-inline')) return;

      const tickerCell = row.querySelector('td.col-ticker') || row.querySelector('td:first-child');
      const ticker = tickerCell ? tickerCell.textContent.trim() : '';
      if (!ticker) return;

      const thesisId = row.dataset.thesisId ? parseInt(row.dataset.thesisId, 10) : null;

      if (actionCell) actionCell.innerHTML = '';

      const wrap = document.createElement('div');
      wrap.className = 'action-btns';

      const bBtn = document.createElement('button');
      bBtn.className   = 'qt-btn-inline qt-btn-buy';
      bBtn.textContent = 'B';
      bBtn.title       = 'Mua thêm ' + ticker;
      bBtn.setAttribute('aria-label', 'Mua ' + ticker);
      bBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        openModal(ticker, 'buy', thesisId, { fromThesisTab: fromThesisTab });
      });

      const sBtn = document.createElement('button');
      sBtn.className   = 'qt-btn-inline qt-btn-sell';
      sBtn.textContent = 'S';
      sBtn.title       = 'Bán ' + ticker;
      sBtn.setAttribute('aria-label', 'Bán ' + ticker);
      sBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        openModal(ticker, 'sell', thesisId, { fromThesisTab: fromThesisTab });
      });

      wrap.appendChild(bBtn);
      wrap.appendChild(sBtn);
      targetCell.appendChild(wrap);
    });
  }

  // ─── Init ────────────────────────────────────────────────────────────────────────────
  function initQuickTrade() {
    ensureModal();
    document.querySelectorAll('[data-holdings-tbody]').forEach(function (tbody) {
      // Init scan cannot know fromThesisTab — defaults to false (Trades behaviour).
      // Renderer calls injectTradeButtons(tbody, { fromThesisTab: true }) for Thesis tbody.
      injectTradeButtons(tbody);
    });
  }

  // ─── Public API ──────────────────────────────────────────────────────────────────────
  global.QuickTrade = {
    init:               initQuickTrade,
    injectTradeButtons: injectTradeButtons,
    openModal:          openModal,
  };

}(window));
