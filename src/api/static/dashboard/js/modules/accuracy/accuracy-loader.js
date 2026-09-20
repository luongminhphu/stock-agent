/**
 * accuracy-loader.js — Wave U3
 * Panel "AI nói gì · Tôi làm gì · Kết quả" — adapter mỏng cho readmodel
 * AccuracyProjection (GET /accuracy?days=N). Không tính rule ở đây: kết luận
 * (summary.verdict/headline/detail) do readmodel quyết định để bot và web
 * nói cùng một câu.
 *
 * Contract đọc:
 *   { days, generated_at, by_source: { core, briefing, pretrade }, trend[], summary }
 */
import { apiBase, getJson } from '../../api/client.js';
import { esc } from '../../utils/format.js';

const RANGES = [7, 30, 90];
let _days = 30;
let _bound = false;

// ─── Format ───────────────────────────────────────────────────────────────

function pct(v) {
  return v == null ? '—' : `${Math.round(v * 100)}%`;
}
function n(v) {
  return Number(v ?? 0).toLocaleString('vi-VN');
}
function hms(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// ─── Render blocks ────────────────────────────────────────────────────────

function verdictHTML(summary = {}) {
  const v = esc(summary.verdict || 'empty');
  const labels = {
    ai_edge: 'AI đang đúng hơn',
    self_edge: 'Bạn đang đúng hơn',
    mixed: 'Chưa phân định',
    low_adoption: 'Ít làm theo',
    insufficient: 'Thiếu dữ liệu',
    empty: 'Chưa có dữ liệu',
  };
  return `
    <div class="acc-verdict" role="status">
      <span class="acc-verdict-tag acc-verdict-tag--${v}">${esc(labels[v] || v)}</span>
      <p class="acc-verdict-headline">${esc(summary.headline || '')}</p>
      <p class="acc-verdict-detail">${esc(summary.detail || '')}</p>
    </div>`;
}

function row(label, value, { sub = '', bar = null, lead = false, muted = false } = {}) {
  const barHTML = bar == null
    ? ''
    : `<div class="acc-bar" aria-hidden="true"><div class="acc-bar-fill${lead ? ' acc-bar-fill--lead' : ''}" style="width:${Math.round(bar * 100)}%"></div></div>`;
  return `
    <div class="acc-row">
      <span class="acc-row-label">${esc(label)}${sub ? `<span class="acc-row-sub">${esc(sub)}</span>` : ''}</span>
      <span class="acc-row-value${muted ? ' acc-row-value--muted' : ''}">${value}</span>
      ${barHTML}
    </div>`;
}

function col(title, meta, rows) {
  return `
    <section class="acc-col" aria-label="${esc(title)}">
      <h3 class="acc-col-title"><span>${esc(title)}</span><span>${esc(meta)}</span></h3>
      ${rows.join('')}
    </section>`;
}

function gridHTML(src = {}) {
  const core = src.core || {};
  const brief = src.briefing || {};
  const pre = src.pretrade || {};
  const totalSignals = (core.total || 0) + (brief.total || 0) + (pre.total || 0);

  const fLead = (pre.followed_hit_rate ?? -1) >= (pre.ignored_hit_rate ?? -1);

  return `
    <div class="acc-grid">
      ${col('AI nói gì', `${n(totalSignals)} tín hiệu`, [
        row('Verdict từ engine', n(core.total), { sub: 'Phân tích mã / thesis' }),
        row('Điểm nhấn trong brief', n(brief.total), { sub: 'Sáng và cuối ngày' }),
        row('Lời khuyên pretrade', n(pre.total), { sub: 'Trước khi đặt lệnh' }),
      ])}
      ${col('Tôi làm gì', 'tỷ lệ hành động', [
        row('Hành động theo verdict', pct(core.acted_rate), {
          sub: `${n(core.acted)} làm · ${n(core.rejected)} bác · ${n(core.not_acted)} bỏ qua`,
          bar: core.acted_rate ?? 0,
        }),
        row('Hành động theo brief', pct(brief.acted_rate), {
          sub: `${n(brief.acted)} làm · ${n(brief.watching)} theo dõi · ${n(brief.skipped)} bỏ qua`,
          bar: brief.acted_rate ?? 0,
        }),
        row('Làm theo pretrade', pct(pre.follow_rate), {
          sub: `${n(pre.followed)} làm theo · ${n(pre.ignored)} bỏ qua`,
          bar: pre.follow_rate ?? 0,
        }),
      ])}
      ${col('Kết quả', `${n(pre.evaluated)} lệnh đã có kết quả`, [
        row('Làm theo AI · đúng', pct(pre.followed_hit_rate), {
          bar: pre.followed_hit_rate ?? 0,
          lead: fLead && pre.followed_hit_rate != null,
        }),
        row('Bỏ qua AI · đúng', pct(pre.ignored_hit_rate), {
          bar: pre.ignored_hit_rate ?? 0,
          lead: !fLead && pre.ignored_hit_rate != null,
        }),
        row('Độ đúng lời khuyên', pct(pre.advice_hit_rate), {
          sub: 'Tất cả lệnh đã reconcile',
          muted: pre.advice_hit_rate == null,
        }),
      ])}
    </div>`;
}

function sparkHTML(trend = [], days = 30) {
  // Điền đủ `days` ngày (kể cả ngày 0 tương tác) để trục thời gian đều.
  const byDay = new Map(trend.map(t => [t.date, Number(t.total) || 0]));
  const today = new Date();
  const series = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    series.push({ key, v: byDay.get(key) || 0 });
  }
  const total = series.reduce((a, b) => a + b.v, 0);
  const max = Math.max(1, ...series.map(s => s.v));
  // Bề rộng cột cố định theo số ngày (5–14px) — không kéo giãn theo panel.
  const unit = Math.min(14, Math.max(5, Math.floor(700 / series.length)));
  const bw = Math.max(3, Math.round(unit * 0.72));
  const W = series.length * unit;
  const H = 28;
  const bars = series.map((s, i) => {
    const h = s.v === 0 ? 1 : Math.max(2, Math.round((s.v / max) * H));
    const cls = i === series.length - 1 ? ' class="acc-spark-bar--today"' : '';
    return `<rect${cls} x="${i * unit}" y="${H - h}" width="${bw}" height="${h}" rx="1"><title>${esc(s.key)}: ${s.v}</title></rect>`;
  }).join('');
  return `
    <div class="acc-spark-wrap">
      <div class="acc-spark-caption"><span class="acc-spark-title">Tương tác với AI theo ngày</span>${n(total)} phản hồi · ${days} ngày gần nhất · cột đậm là hôm nay</div>
      <svg class="acc-spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img"
           aria-label="Số lần phản hồi AI theo ngày, ${days} ngày gần nhất">${bars}</svg>
    </div>`;
}

function footerHTML(generatedAt) {
  return `
    <div class="acc-footer">
      <span>Cập nhật ${esc(hms(generatedAt))} · nguồn: ledger phản hồi + decision log</span>
      <button type="button" class="acc-link" data-acc-open-memory>Xem AI đã học được gì từ tôi</button>
    </div>`;
}

function rangeHTML(days) {
  return RANGES.map(d =>
    `<button type="button" class="acc-range-btn${d === days ? ' active' : ''}" data-acc-days="${d}" aria-pressed="${d === days}">${d} ngày</button>`,
  ).join('');
}

function skeletonHTML() {
  return `
    <div class="acc-verdict"><span class="acc-skel" style="width:96px;height:20px"></span><span class="acc-skel" style="width:60%"></span></div>
    <div class="acc-grid">
      ${[0, 1, 2].map(() => `<div class="acc-col"><span class="acc-skel" style="width:40%"></span><span class="acc-skel"></span><span class="acc-skel"></span><span class="acc-skel"></span></div>`).join('')}
    </div>`;
}

function errorHTML(msg) {
  return `
    <div class="acc-error" role="alert">
      <span>Không tải được dữ liệu phản hồi${msg ? ` — ${esc(msg)}` : ''}.</span>
      <button type="button" class="acc-link" data-acc-retry>Thử lại</button>
    </div>`;
}

// ─── Load ─────────────────────────────────────────────────────────────────

function _bind(panel) {
  if (_bound) return;
  _bound = true;
  panel.addEventListener('click', ev => {
    const rangeBtn = ev.target.closest('[data-acc-days]');
    if (rangeBtn) {
      const d = Number(rangeBtn.dataset.accDays);
      if (RANGES.includes(d) && d !== _days) {
        _days = d;
        loadAccuracy();
      }
      return;
    }
    if (ev.target.closest('[data-acc-retry]')) {
      loadAccuracy();
      return;
    }
    if (ev.target.closest('[data-acc-open-memory]')) {
      const det = document.getElementById('memoryDetails');
      if (det) {
        det.open = true;
        det.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  });
}

export async function loadAccuracy() {
  const panel = document.getElementById('accuracyPanel');
  const body = document.getElementById('accuracyBody');
  const range = document.getElementById('accuracyRange');
  if (!panel || !body) return;
  _bind(panel);

  if (range) range.innerHTML = rangeHTML(_days);
  body.innerHTML = skeletonHTML();
  panel.setAttribute('aria-busy', 'true');

  try {
    const data = await getJson(`${apiBase()}/accuracy?days=${_days}`);
    if (!data) {
      body.innerHTML = errorHTML('');
      return;
    }
    const summary = data.summary || {};
    const isEmpty = summary.verdict === 'empty';
    body.innerHTML = verdictHTML(summary)
      + (isEmpty ? '' : gridHTML(data.by_source))
      + (isEmpty ? '' : sparkHTML(data.trend, data.days || _days))
      + footerHTML(data.generated_at);
  } catch (err) {
    body.innerHTML = errorHTML(err?.message || '');
  } finally {
    panel.removeAttribute('aria-busy');
  }
}
