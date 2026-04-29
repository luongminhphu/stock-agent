// api/client.js — HTTP client wrapper cho stock-agent API
// Owner: api segment (không chứa business/render logic)

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
 * Headers mặc định cho mọi request
 * @returns {Record<string, string>}
 */
export function authHeaders() {
  return { 'Content-Type': 'application/json' };
}

/**
 * Fetch JSON với error handling chuẩn
 * Throw Error nếu response không ok
 * @param {string} url
 * @param {RequestInit} options
 * @returns {Promise<any|null>}
 */
export async function getJson(url, options = {}) {
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
