// api/client.js — HTTP client wrapper cho stock-agent API
// Owner: api segment (đượng không chứa business/render logic)

const DEFAULT_HEADERS = Object.freeze({ 'Content-Type': 'application/json' });
const inflightGetRequests = new Map();

/**
 * Base URL cho readmodel dashboard endpoints
 * @returns {string}
 */
export function apiBase() {
  return '/api/v1/readmodel/dashboard';
}

/**
 * Base URL cho thesis endpoints
 * @returns {string}
 */
export function thesisApiBase() {
  return '/api/v1/thesis';
}

/**
 * Base URL cho readmodel endpoints (non-dashboard)
 * e.g. /thesis/{id}/conviction-timeline, /thesis/{id}/review-timeline
 * @returns {string}
 */
export function readmodelApiBase() {
  return '/api/v1/readmodel';
}

/**
 * Base URL cho briefing endpoints
 * @returns {string}
 */
export function briefingApiBase() {
  return '/api/v1/briefing';
}

/**
 * Base URL cho memory endpoints
 * @returns {string}
 */
export function memoryApiBase() {
  return '/api/v1/memory';
}

/**
 * Base URL cho market endpoints
 * @returns {string}
 */
export function marketApiBase() {
  return '/api/v1/market';
}

/**
 * Headers mặc định cho mọi request
 * @returns {Record<string, string>}
 */
export function authHeaders() {
  return DEFAULT_HEADERS;
}

function buildRequestKey(url, options = {}) {
  const method = (options.method ?? 'GET').toUpperCase();
  return `${method}:${url}`;
}

/**
 * Rút gọn thân lỗi thành một câu đọc được (giữ tiền tố mã HTTP ở caller).
 * - JSON {"detail": "..."} của FastAPI → chuỗi detail
 * - Trang HTML (Cloudflare / reverse proxy khi API khởi động lại, quá tải) → câu thông báo
 */
function _errorText(status, raw) {
  const text = String(raw ?? '').trim();
  if (text.startsWith('<') || /<html/i.test(text.slice(0, 200))) {
    if (status === 502 || status === 503 || status === 504 || status === 520 || status === 522 || status === 524) {
      return 'Máy chủ tạm thời không phản hồi (đang khởi động lại hoặc quá tải) — thử lại sau ít phút.';
    }
    return 'Máy chủ trả về lỗi không mong đợi.';
  }
  try {
    const d = JSON.parse(text).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(x => x?.msg ?? String(x)).join('; ');
  } catch { /* không phải JSON */ }
  return text.length > 300 ? `${text.slice(0, 300)}…` : text;
}

async function fetchJson(url, options = {}) {
  const method = (options.method ?? 'GET').toUpperCase();
  const r = await fetch(url, {
    ...options,
    // Prevent browser from serving stale GET responses from HTTP cache.
    // API data changes frequently (market prices, thesis state) — always
    // go to the network. Server-side TTL cache handles repeat-fetch cost.
    cache: method === 'GET' ? 'no-store' : (options.cache ?? 'default'),
    headers: { ...authHeaders(), ...(options.headers ?? {}) },
  });
  if (!r.ok) {
    const raw = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status} ${_errorText(r.status, raw)}`);
  }
  if (r.status === 204 || r.headers.get('content-length') === '0') return null;
  return r.json();
}

/**
 * Fetch JSON với error handling chuẩn
 * Throw Error nếu response không ok
 * PERF Wave 2:
 * - Deduplicate in-flight GET requests theo method+url
 * - Reuse default headers object
 * @param {string} url
 * @param {RequestInit} options
 * @returns {Promise<any|null>}
 */
export async function getJson(url, options = {}) {
  const method = (options.method ?? 'GET').toUpperCase();
  const isGet = method === 'GET' && options.body == null;
  const requestKey = buildRequestKey(url, options);

  if (isGet && inflightGetRequests.has(requestKey)) {
    return inflightGetRequests.get(requestKey);
  }

  const promise = fetchJson(url, options)
    .finally(() => {
      if (isGet) inflightGetRequests.delete(requestKey);
    });

  if (isGet) inflightGetRequests.set(requestKey, promise);
  return promise;
}

/**
 * Gửi JSON body (POST/PUT/PATCH/DELETE)
 * @param {string} url
 * @param {string} method
 * @param {any} body
 * @returns {Promise<any|null>}
 */
export async function sendJson(url, method, body) {
  return getJson(url, {
    method,
    body: body != null ? JSON.stringify(body) : undefined,
  });
}
