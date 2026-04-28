'use strict';


function el(id) { return document.getElementById(id); }
function apiBase() { return '/api/v1/readmodel/dashboard'; }
function thesisApiBase() { return '/api/v1/thesis'; }
function authHeaders() { return { 'Content-Type': 'application/json' }; }

async function getJson(url, options = {}) {
  const r = await fetch(url, { ...options, headers: { ...authHeaders(), ...(options.headers ?? {}) } });
  if (!r.ok) {
    const msg = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status} ${msg}`);
  }
  if (r.status === 204 || r.headers.get('content-length') === '0') return null;
  return r.json();
}

async function sendJson(url, method, body) {
  return getJson(url, { method, body: body != null ? JSON.stringify(body) : undefined });
}

function fmt(n, decimals = 0) {
  if (n == null) return '\u2014';
  return Number(n).toLocaleString('vi-VN', { maximumFractionDigits: decimals });
}

function fmtDate(d) {
  if (!d) return '\u2014';
  return new Date(d).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function badge(val) {
  const cls = String(val || '').toLowerCase();
  return `<span class="badge ${cls}">${val || '\u2014'}</span>`;
}

function esc(v) {
  return String(v ?? '').replace(/[&<>'"]/g, s => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[s]));
}

function normalizeSummary(text) {
  if (!text) return '';
  return text.replace(/(?<!\n)\n(?!\n)/g, ' ').trim();
}

function highlightScanText(text) {
  if (!text) return esc(text);
  const normalized = text.replace(/(?<!\n)\n(?!\n)/g, ' ').trim();
  return esc(normalized)
    .replace(/\b([A-Z]{2,5})(?=:|\s|,|;)/g,
      '<strong style="color:#7dd3fc;font-weight:800;letter-spacing:.04em;">$1</strong>')
    .replace(/(-\d+(?:\.\d+)?%?)/g,
      '<span style="color:#fb923c;font-weight:600;">$1</span>')
    .replace(/(\+\d+(?:\.\d+)?%?)/g,
      '<span style="color:#4ade80;font-weight:700;">$1</span>');
}

function showToast(msg, type = 'success', ms = 3000) {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

function openModal(id) { const d = el(id); if (d) d.showModal(); }
function closeModal(id) { const d = el(id); if (d) d.close(); }

let _selectedThesisId = null;
let _theses = [];
let _deleteCallback = null;

// NEW: cache review + selections cho AI apply flow
let latestAiReviews = {};       // key: thesisId -> latest review payload
let aiApplyThesisId = null;     // thesis đang mở modal AI apply
let aiSelectedRecIds = [];      // rec ids user tick trong modal
