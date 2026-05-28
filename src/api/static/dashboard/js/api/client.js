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

async function fetchJson(url, options = {}) {
  const r = await fetch(url, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers ?? {}) },
  });
  if (!r.ok) {
    const msg = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status} ${msg}`);
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
