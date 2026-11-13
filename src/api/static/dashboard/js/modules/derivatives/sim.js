/**
 * sim.js — Tab "Paper Trading": giả lập HĐTL VN30 tự động theo base rate.
 * Owner: modules/derivatives
 *
 * Nguồn (prefix /api/v1/derivatives):
 *   GET  /sim        → { config, primary, open, trades, stats, ticks, books: {base, fade} }
 *   PUT  /sim        → cập nhật cấu hình (chỉ trường gửi lên; null = tắt) → trả lại view
 *   POST /sim/reset  → xoá lệnh + đường P&L, tắt giả lập
 *
 * Bot quét 5 phút/lần trong giờ giao dịch; panel tự làm mới mỗi 60 giây khi tab đang mở.
 *
 * Exports:
 *   loadSim() — lần đầu dựng khung trong #paperRoot + bind sự kiện + hẹn giờ làm mới; sau đó tải lại
 */

import { getJson, sendJson } from '../../api/client.js?v=1';
import { showToast } from '../../utils/dom.js?v=1';
import { esc } from '../../utils/format.js?v=1';
import {
  fmtNum, fmtVnd, fmtPct, fmtProb, signClass, suggestionBadge,
} from '../board/baserate-drawer.js?v=2';

const URL_SIM = '/api/v1/derivatives/sim';
const REFRESH_MS = 60_000;
const BOOK_LABEL = Object.freeze({ base: 'Follow (theo tín hiệu)', fade: 'Fade (đánh ngược)' });
const BOOK_SHORT = Object.freeze({ base: 'Follow', fade: 'Fade' });
const MODE_LABEL = Object.freeze({
  follow: 'Follow — theo tín hiệu',
  fade: 'Fade — đánh ngược tín hiệu',
  auto: 'Auto — theo sổ vượt trội',
});
const REASON_LABEL = Object.freeze({
  signal: 'Signal', stop: 'Stop-loss', target: 'Take-profit', roll: 'Roll', stopped: 'Dừng giả lập',
});
const SERIES = ['F1M', 'F2M', 'F1Q', 'F2Q'];

const _s = { data: null, bound: false, timer: null, seq: 0, busy: false };

const fmtPts = (v) => fmtNum(v, 1);
const fmtSignedVnd = (v) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${fmtVnd(v)} đ`);
const fmtTime = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
};
const sideTag = (side) => (side
  ? `<span class="dv-side dv-side--${side === 'LONG' ? 'long' : 'short'}">${side === 'LONG' ? 'Long' : 'Short'}</span>`
  : '<span class="dv-muted">Flat</span>');

// ── Khung ───────────────────────────────────────────────────────────────────
function _shellHTML() {
  return `
    <section class="panel dv-panel" aria-labelledby="dvSimTitle">
      <div class="panel-header split-header">
        <div>
          <p class="eyebrow">Paper trading · quét 5 phút trong giờ giao dịch</p>
          <h2 id="dvSimTitle">Giả lập tự động VN30F theo base rate</h2>
        </div>
        <div class="dv-sim-actions" id="dvSimActions"></div>
      </div>
      <div class="panel-body">
        <details class="dv-sim-settings" id="dvSimSettings">
          <summary>Cài đặt chiến lược</summary>
          <form class="dv-calc dv-sim-form" id="dvSimForm" novalidate>
            <label class="dv-field"><span class="dv-label">Kỳ hạn</span>
              <select name="series">${SERIES.map(s => `<option value="${s}">${s}</option>`).join('')}</select></label>
            <label class="dv-field"><span class="dv-label">Số HĐ</span>
              <input name="qty" type="number" inputmode="numeric" min="1" max="1000" step="1" required /></label>
            <label class="dv-field dv-field--wide"><span class="dv-label">Sổ chính (gửi cảnh báo)</span>
              <select name="mode">${Object.entries(MODE_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></label>
            <label class="dv-field"><span class="dv-label">Stop-loss (điểm)</span>
              <input name="stop_points" type="number" inputmode="decimal" min="0" step="0.5" placeholder="Tắt" /></label>
            <label class="dv-field"><span class="dv-label">Take-profit (điểm)</span>
              <input name="target_points" type="number" inputmode="decimal" min="0" step="0.5" placeholder="Tắt" /></label>
            <label class="dv-field"><span class="dv-label">Stop theo ATR (×)</span>
              <input name="stop_atr" type="number" inputmode="decimal" min="0" step="0.1" placeholder="Dùng điểm" /></label>
            <label class="dv-field"><span class="dv-label">Target theo ATR (×)</span>
              <input name="target_atr" type="number" inputmode="decimal" min="0" step="0.1" placeholder="Dùng điểm" /></label>
            <label class="dv-field"><span class="dv-label">Lọc đám đông (× ATR)</span>
              <input name="crowd_atr" type="number" inputmode="decimal" min="0" step="0.1" placeholder="Tắt" /></label>
            <label class="dv-field"><span class="dv-label">Cảnh báo P&amp;L/Margin (%)</span>
              <input name="alert_margin_pct" type="number" inputmode="decimal" min="0" step="1" placeholder="Tắt" /></label>
            <label class="dv-check"><input name="avoid_round" type="checkbox" /> Né số tròn khi đặt Stop/Target</label>
            <div class="dv-field dv-field--submit"><button type="submit" class="primary-btn">Lưu cài đặt</button></div>
          </form>
          <ul class="dv-note dv-sim-help">
            <li><strong>Follow</strong> theo tín hiệu base rate VN30 (Bullish → Long, Bearish → Short, Neutral → đứng ngoài). <strong>Fade</strong> luôn chạy song song và đánh ngược — so win rate hai sổ để biết tín hiệu đám đông còn lợi thế hay đang bị đánh ngược. <strong>Auto</strong> chỉ chuyển sang Fade khi mỗi sổ ≥ 20 lệnh và Fade vượt trội rõ (win rate +10 điểm %, tổng P&amp;L cao hơn).</li>
            <li><strong>Né số tròn</strong>: mức Stop sát bội 5 điểm được đẩy ra xa hơn (tránh vùng quét stop), Target kéo về gần hơn (khớp trước vùng cản). ATR = biên độ trung bình 14 phiên của VN30; đặt ATR thì ưu tiên hơn điểm cố định.</li>
            <li><strong>Lọc đám đông</strong>: sổ Follow không mở mới khi VN30 trong phiên đã chạy cùng chiều tín hiệu ≥ ngưỡng × ATR.</li>
          </ul>
        </details>
        <div id="dvSim" aria-live="polite"><p class="dv-loading" aria-busy="true"><span class="dv-spinner" aria-hidden="true"></span>Đang tải…</p></div>
      </div>
    </section>`;
}

// ── Render ──────────────────────────────────────────────────────────────────
function _actionsHTML(cfg) {
  const on = !!cfg?.active;
  return `
    <span class="dv-sim-pill ${on ? 'is-on' : ''}">${on ? 'Đang chạy' : 'Đã dừng'}</span>
    <button type="button" class="${on ? 'ghost-btn' : 'primary-btn'}" data-sim-toggle="${on ? '0' : '1'}">${on ? 'Dừng' : 'Bắt đầu'}</button>
    <button type="button" class="ghost-btn" data-sim-reset>Reset</button>`;
}

function _fillForm(cfg) {
  const f = document.getElementById('dvSimForm');
  if (!f || !cfg || f.contains(document.activeElement)) return; // đang gõ → không ghi đè
  f.series.value = cfg.series ?? 'F1M';
  f.qty.value = cfg.qty ?? 1;
  f.mode.value = cfg.mode ?? 'follow';
  for (const k of ['stop_points', 'target_points', 'stop_atr', 'target_atr', 'crowd_atr', 'alert_margin_pct']) {
    f[k].value = cfg[k] ?? '';
  }
  f.avoid_round.checked = cfg.avoid_round !== false;
}

function _positionHTML(d) {
  const cfg = d.config ?? {};
  const o = d.open;
  const primary = d.primary ?? 'base';
  const head = `
    <div><dt>Tín hiệu VN30</dt><dd>${cfg.last_signal ? suggestionBadge(cfg.last_signal) : '<span class="dv-muted">chưa quét</span>'}</dd></div>
    <div><dt>Sổ chính</dt><dd>${esc(BOOK_SHORT[primary])}<span class="dv-sub">${esc(MODE_LABEL[cfg.mode] ?? '')}</span></dd></div>`;
  if (!o) {
    return `<dl class="dv-kpis">${head}
      <div><dt>Vị thế</dt><dd>${sideTag(null)}${cfg.reentry_blocked_today ? '<span class="dv-sub">Đã chạm Stop/Target — chờ phiên sau</span>' : ''}</dd></div>
      <div><dt>Lần quét gần nhất</dt><dd>${fmtTime(cfg.last_run_at)}</dd></div>
    </dl>`;
  }
  return `<dl class="dv-kpis">${head}
    <div><dt>Vị thế</dt><dd>${sideTag(o.side)} ${fmtNum(o.qty)} × <span class="dv-code">${esc(o.contract_code)}</span></dd></div>
    <div><dt>Entry → Mark</dt><dd>${fmtPts(o.entry_price)} → ${fmtPts(o.mark_price)}</dd></div>
    <div><dt>Stop / Target</dt><dd>${o.stop_price == null ? '—' : fmtPts(o.stop_price)} / ${o.target_price == null ? '—' : fmtPts(o.target_price)}</dd></div>
    <div><dt>P&amp;L chưa chốt</dt><dd class="${signClass(o.unrealized_pnl)}">${fmtSignedVnd(o.unrealized_pnl)}</dd></div>
    <div><dt>P&amp;L trên Margin</dt><dd class="${signClass(o.pnl_on_margin_pct)}">${o.pnl_on_margin_pct == null ? '—' : `${o.pnl_on_margin_pct > 0 ? '+' : ''}${fmtPct(o.pnl_on_margin_pct)}`}<span class="dv-sub">Initial margin ${fmtVnd(o.initial_margin)} đ</span></dd></div>
    <div><dt>Lần quét gần nhất</dt><dd>${fmtTime(cfg.last_run_at)}</dd></div>
  </dl>`;
}

/** Đường equity (đã chốt + chưa chốt) hai sổ, cùng một trục. */
const fmtMil = (v) => `${fmtNum(v / 1e6, Math.abs(v) >= 1e7 ? 0 : 1)} tr`;

function _chartHTML(books, primary, width = 720) {
  const series = ['base', 'fade']
    .map(b => ({ b, pts: (books?.[b]?.ticks ?? []).map(t => ({ t: new Date(t.at).getTime(), v: Number(t.equity) || 0 })) }))
    .filter(s => s.pts.length);
  if (!series.length) {
    return '<p class="dv-state">Chưa có dữ liệu. Bấm <strong>Bắt đầu</strong> — bot quét 5 phút/lần trong giờ giao dịch (08:45–11:30, 13:00–14:45).</p>';
  }
  // Vẽ đúng kích thước thật của khung → chữ không phóng to/thu nhỏ theo viewBox.
  const W = Math.max(300, Math.round(width)), H = W < 520 ? 180 : 230, L = 52, R = 56, T = 12, B = 24;
  const all = series.flatMap(s => s.pts);
  const t0 = Math.min(...all.map(p => p.t)), t1 = Math.max(...all.map(p => p.t));
  let lo = Math.min(0, ...all.map(p => p.v)), hi = Math.max(0, ...all.map(p => p.v));
  if (lo === hi) { lo -= 1_000_000; hi += 1_000_000; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const x = (t) => (t1 === t0 ? L + (W - L - R) / 2 : L + ((t - t0) / (t1 - t0)) * (W - L - R));
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const ticks = [lo + pad, 0, hi - pad].filter((v, i, a) => a.indexOf(v) === i);
  // Nhãn cuối đường: tách ra ≥ 14px khi hai đường kết thúc sát nhau (vd cùng 0 đ khi Flat).
  const labelY = series.map(s => y(s.pts[s.pts.length - 1].v) + 4);
  if (labelY.length === 2 && Math.abs(labelY[0] - labelY[1]) < 14) {
    const mid = (labelY[0] + labelY[1]) / 2;
    const up = series[0].pts.at(-1).v >= series[1].pts.at(-1).v ? 0 : 1;
    labelY[up] = mid - 7;
    labelY[1 - up] = mid + 7;
  }
  const lines = series.map((s, k) => {
    const d = s.pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
    const last = s.pts[s.pts.length - 1];
    return `<path class="dv-sim-line dv-sim-line--${s.b}${s.b === primary ? ' is-primary' : ''}" d="${d}" />
      <circle class="dv-sim-dot dv-sim-dot--${s.b}" cx="${x(last.t).toFixed(1)}" cy="${y(last.v).toFixed(1)}" r="4" />
      <text class="dv-sim-label" x="${(x(last.t) + 8).toFixed(1)}" y="${labelY[k].toFixed(1)}">${BOOK_SHORT[s.b]}</text>`;
  }).join('');
  const data = JSON.stringify(series.map(s => ({ b: s.b, pts: s.pts })));
  return `
    <div class="dv-sim-legend" aria-hidden="true">
      ${series.map(s => `<span><i class="dv-sim-key dv-sim-key--${s.b}"></i>${esc(BOOK_LABEL[s.b])}${s.b === primary ? ' · sổ chính' : ''}</span>`).join('')}
    </div>
    <div class="dv-sim-chart" data-sim-chart='${esc(data)}' data-geom="${[W, H, L, R, T, B, t0, t1, lo, hi].join(',')}">
      <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Đường P&amp;L tích luỹ hai sổ Follow và Fade">
        ${ticks.map(v => `<line class="dv-sim-grid${v === 0 ? ' is-zero' : ''}" x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" />
          <text class="dv-sim-axis" x="${L - 6}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end">${fmtMil(v)}</text>`).join('')}
        <text class="dv-sim-axis" x="${L}" y="${H - 6}">${fmtTime(new Date(t0).toISOString())}</text>
        <text class="dv-sim-axis" x="${W - R}" y="${H - 6}" text-anchor="end">${fmtTime(new Date(t1).toISOString())}</text>
        ${lines}
        <line class="dv-sim-cross" x1="0" x2="0" y1="${T}" y2="${H - B}" />
      </svg>
      <div class="dv-sim-tip" role="status" hidden></div>
    </div>`;
}

function _statsHTML(books, primary) {
  const b = books?.base?.stats ?? {}, f = books?.fade?.stats ?? {};
  const row = (label, fn, cls = () => '') => `<tr><th scope="row">${label}</th>
    <td class="num ${cls(b)}">${fn(b)}</td><td class="num ${cls(f)}">${fn(f)}</td></tr>`;
  const pf = (s) => (s.profit_factor == null ? '—' : fmtNum(s.profit_factor, 2));
  const reasons = (s) => ['signal', 'stop', 'target', 'roll']
    .map(r => `${REASON_LABEL[r]} ${fmtNum(s.by_reason?.[r] ?? 0)}`).join(' · ');
  const head = (k) => `<i class="dv-sim-key dv-sim-key--${k}" aria-hidden="true"></i>${esc(BOOK_SHORT[k])}${k === primary ? ' <span class="dv-sim-pill is-on">chính</span>' : ''}<span class="dv-sub">${k === 'base' ? 'theo tín hiệu' : 'đánh ngược'}</span>`;
  return `
    <div class="tbl-scroll" tabindex="0" role="region" aria-label="So sánh thống kê hai sổ">
      <table class="dv-table dv-sim-stats">
        <thead><tr><th scope="col">Chỉ số</th><th scope="col" class="num">${head('base')}</th><th scope="col" class="num">${head('fade')}</th></tr></thead>
        <tbody>
          ${row('Số lệnh đã đóng', s => `${fmtNum(s.trades ?? 0)} <span class="dv-sub">${fmtNum(s.wins ?? 0)} thắng / ${fmtNum(s.losses ?? 0)} thua</span>`)}
          ${row('Win rate', s => fmtProb(s.win_rate))}
          ${row('Win rate Long', s => fmtProb(s.win_rate_long))}
          ${row('Win rate Short', s => fmtProb(s.win_rate_short))}
          ${row('Avg win / Avg loss', s => `${s.avg_win == null ? '—' : fmtSignedVnd(s.avg_win)} / ${s.avg_loss == null ? '—' : fmtSignedVnd(s.avg_loss)}`)}
          ${row('Profit factor', pf)}
          ${row('Tổng P&amp;L đã chốt', s => fmtSignedVnd(s.total_pnl ?? 0), s => signClass(s.total_pnl))}
          ${row('Max drawdown', s => fmtSignedVnd(s.max_drawdown ?? 0), s => signClass(s.max_drawdown))}
          ${row('Lý do thoát', reasons)}
        </tbody>
      </table>
    </div>
    <p class="dv-note">P&amp;L đã trừ phí ước tính ${fmtVnd(_s.data?.config?.fee_per_side)} đ/HĐ/chiều. Win rate cần đủ mẫu (≥ 20 lệnh) mới có ý nghĩa — vài lệnh đầu chủ yếu là may rủi.</p>`;
}

function _tradesHTML(books) {
  const rows = ['base', 'fade']
    .flatMap(b => (books?.[b]?.trades ?? []).map(t => ({ ...t, book: b })))
    .sort((a, c) => String(c.exit_at).localeCompare(String(a.exit_at)))
    .slice(0, 60);
  if (!rows.length) return '<p class="dv-state">Chưa có lệnh nào đóng.</p>';
  return `
    <div class="tbl-scroll" tabindex="0" role="region" aria-label="Lệnh giả lập đã đóng">
      <table class="dv-table">
        <thead><tr>
          <th scope="col">Sổ</th><th scope="col">HĐ</th><th scope="col">Side</th><th scope="col" class="num">Số HĐ</th>
          <th scope="col" class="num">Entry</th><th scope="col" class="num">Exit</th><th scope="col">Lý do</th>
          <th scope="col">Thời gian</th><th scope="col" class="num">P&amp;L</th>
        </tr></thead>
        <tbody>${rows.map(t => `<tr>
          <td><i class="dv-sim-key dv-sim-key--${t.book}" aria-hidden="true"></i>${esc(BOOK_SHORT[t.book])}</td>
          <td class="dv-code">${esc(t.contract_code)}</td>
          <td>${sideTag(t.side)}</td>
          <td class="num">${fmtNum(t.qty)}</td>
          <td class="num">${fmtPts(t.entry_price)}</td>
          <td class="num">${fmtPts(t.exit_price)}</td>
          <td>${esc(REASON_LABEL[t.exit_reason] ?? t.exit_reason ?? '')}</td>
          <td>${fmtTime(t.entry_at)} → ${fmtTime(t.exit_at)}</td>
          <td class="num ${signClass(t.realized_pnl)}">${fmtSignedVnd(t.realized_pnl)}</td>
        </tr>`).join('')}</tbody>
      </table>
    </div>`;
}

function _render(d) {
  _s.data = d;
  const act = document.getElementById('dvSimActions');
  if (act) act.innerHTML = _actionsHTML(d.config);
  _fillForm(d.config);
  const wrap = document.getElementById('dvSim');
  if (!wrap) return;
  const primary = d.primary ?? 'base';
  wrap.innerHTML = `
    ${_positionHTML(d)}
    <h3 class="dv-sim-h">Đường P&amp;L tích luỹ</h3>
    ${_chartHTML(d.books, primary, wrap.clientWidth || 720)}
    <h3 class="dv-sim-h">Thống kê Follow vs Fade</h3>
    ${_statsHTML(d.books, primary)}
    <h3 class="dv-sim-h">Lệnh đã đóng</h3>
    ${_tradesHTML(d.books)}`;
}

// ── Hover trên biểu đồ ──────────────────────────────────────────────────────
function _onChartMove(e) {
  const box = e.target.closest?.('[data-sim-chart]');
  if (!box) return;
  const svg = box.querySelector('svg');
  const tip = box.querySelector('.dv-sim-tip');
  const cross = box.querySelector('.dv-sim-cross');
  if (!svg || !tip || !cross) return;
  const [W, , L, R, , , t0, t1] = box.dataset.geom.split(',').map(Number);
  const rect = svg.getBoundingClientRect();
  const px = ((e.clientX - rect.left) / rect.width) * W;
  const t = t1 === t0 ? t0 : t0 + ((px - L) / (W - L - R)) * (t1 - t0);
  const series = JSON.parse(box.dataset.simChart);
  const near = series.map(s => {
    let best = s.pts[0];
    for (const p of s.pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    return { b: s.b, p: best };
  });
  const at = near[0].p.t;
  const cx = t1 === t0 ? L + (W - L - R) / 2 : L + ((at - t0) / (t1 - t0)) * (W - L - R);
  cross.setAttribute('x1', cx); cross.setAttribute('x2', cx);
  box.classList.add('is-hover');
  tip.hidden = false;
  tip.innerHTML = `<strong>${fmtTime(new Date(at).toISOString())}</strong>${near.map(n =>
    `<span><i class="dv-sim-key dv-sim-key--${n.b}"></i>${BOOK_SHORT[n.b]} <b>${fmtSignedVnd(n.p.v)}</b></span>`).join('')}`;
  const left = (cx / W) * rect.width;
  tip.style.left = `${Math.min(Math.max(left, 70), rect.width - 70)}px`;
}

function _onChartLeave(e) {
  const box = e.target.closest?.('[data-sim-chart]');
  if (!box || box.contains(e.relatedTarget)) return;
  box.querySelector('.dv-sim-tip').hidden = true;
  box.classList.remove('is-hover');
}

// ── Gửi cấu hình ────────────────────────────────────────────────────────────
async function _put(body, okMsg) {
  if (_s.busy) return;
  _s.busy = true;
  try {
    const d = await sendJson(URL_SIM, 'PUT', body);
    _render(d);
    if (okMsg) showToast(okMsg, 'success');
  } catch (err) {
    showToast(`Không lưu được: ${err?.message ?? ''}`, 'error');
  } finally {
    _s.busy = false;
  }
}

function _formBody(f) {
  const num = (k) => (f[k].value === '' ? null : Number(f[k].value));
  return {
    series: f.series.value,
    qty: parseInt(f.qty.value, 10) || 1,
    mode: f.mode.value,
    stop_points: num('stop_points'),
    target_points: num('target_points'),
    stop_atr: num('stop_atr'),
    target_atr: num('target_atr'),
    crowd_atr: num('crowd_atr'),
    alert_margin_pct: num('alert_margin_pct'),
    avoid_round: f.avoid_round.checked,
  };
}

function _bind() {
  if (_s.bound) return;
  _s.bound = true;
  const root = document.getElementById('paperRoot');
  if (!root) return;
  root.addEventListener('submit', (e) => {
    if (e.target.id !== 'dvSimForm') return;
    e.preventDefault();
    _put(_formBody(e.target), 'Đã lưu cài đặt giả lập.');
  });
  root.addEventListener('click', async (e) => {
    if (e.target.closest('[data-dv-retry="sim"]')) { loadSim(); return; }
    const tog = e.target.closest('[data-sim-toggle]');
    if (tog) {
      const on = tog.dataset.simToggle === '1';
      if (!on && !window.confirm('Dừng giả lập? Vị thế đang mở (cả hai sổ) sẽ được đóng theo giá hiện tại.')) return;
      _put({ active: on }, on ? 'Đã bật giả lập — bot quét 5 phút/lần trong giờ giao dịch.' : 'Đã dừng giả lập.');
      return;
    }
    if (e.target.closest('[data-sim-reset]')) {
      if (!window.confirm('Xoá toàn bộ lệnh và đường P&L giả lập? Không hoàn tác được.')) return;
      try {
        _render(await sendJson(`${URL_SIM}/reset`, 'POST', {}));
        showToast('Đã reset giả lập.', 'success');
      } catch (err) {
        showToast(`Không reset được: ${err?.message ?? ''}`, 'error');
      }
    }
  });
  root.addEventListener('pointermove', _onChartMove);
  root.addEventListener('pointerout', _onChartLeave);
  _s.timer = setInterval(() => {
    const visible = document.visibilityState === 'visible' && root.offsetParent !== null;
    if (visible && !_s.busy) loadSim({ silent: true });
  }, REFRESH_MS);
}

export async function loadSim({ silent = false } = {}) {
  const root = document.getElementById('paperRoot');
  if (root && !root.dataset.ready) {
    root.innerHTML = _shellHTML();
    root.dataset.ready = '1';
  }
  _bind();
  const wrap = document.getElementById('dvSim');
  if (!wrap) return;
  const seq = ++_s.seq;
  try {
    const d = await getJson(URL_SIM);
    if (seq !== _s.seq) return;
    _render(d ?? {});
  } catch (err) {
    if (seq !== _s.seq || silent) return;
    wrap.innerHTML = `<div class="dv-state dv-state--error" role="alert">
      <p>Không tải được dữ liệu giả lập.</p><p class="dv-state-detail">${esc(err?.message)}</p>
      <button type="button" class="ghost-btn" data-dv-retry="sim">Thử lại</button></div>`;
  }
}
