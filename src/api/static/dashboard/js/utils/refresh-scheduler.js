/**
 * refresh-scheduler.js — Shared auto-refresh coordinator (Wave 5c)
 *
 * Vấn đề trước đây: mỗi module tự quản 1 setInterval riêng (engine heartbeat
 * 1 phút, attention 5 phút, recommendations 5 phút, today-loop 10 phút).
 * Khi tab ẩn (visibilityState=hidden) các interval vẫn bắn request —
 * tốn battery, tốn API calls, và khi user quay lại thì 4 panel cùng
 * refresh 1 lúc gây burst.
 *
 * Scheduler này:
 *   - Gom mọi periodic refresh về 1 registry.
 *   - Khi tab hidden: pause toàn bộ timers.
 *   - Khi tab visible trở lại: fire ngay callback bị miss (nếu quá hạn)
 *     rồi resume chu kỳ.
 *   - Giới hạn burst: khi resume, các callback quá hạn được chạy so le
 *     500ms thay vì đồng loạt.
 *
 * Usage:
 *   import { RefreshScheduler } from '../utils/refresh-scheduler.js';
 *   RefreshScheduler.register('attention', loadAttentionPanel, 5 * 60 * 1000);
 *   RefreshScheduler.unregister('attention');
 *
 * Callback nhận { silent: true } khi được gọi theo chu kỳ (để module
 * skip skeleton / spinner nếu muốn).
 */

const _jobs = new Map(); // name -> { cb, intervalMs, timerId, lastRunAt }

const RESUME_STAGGER_MS = 500;

function _clearTimer(job) {
  if (job.timerId != null) {
    clearTimeout(job.timerId);
    job.timerId = null;
  }
}

function _schedule(job) {
  _clearTimer(job);
  if (document.visibilityState === 'hidden') return; // paused
  job.timerId = setTimeout(async () => {
    job.lastRunAt = Date.now();
    try { await job.cb({ silent: true }); } catch { /* callback tự xử lý lỗi */ }
    _schedule(job);
  }, job.intervalMs);
}

function _pauseAll() {
  for (const job of _jobs.values()) _clearTimer(job);
}

function _resumeAll() {
  let stagger = 0;
  for (const job of _jobs.values()) {
    const overdue = job.lastRunAt == null
      ? false
      : (Date.now() - job.lastRunAt) >= job.intervalMs;
    if (overdue) {
      // Chạy ngay nhưng so le để tránh burst khi user mở lại tab
      _clearTimer(job);
      const delay = stagger;
      stagger += RESUME_STAGGER_MS;
      job.timerId = setTimeout(async () => {
        job.lastRunAt = Date.now();
        try { await job.cb({ silent: true }); } catch { /* noop */ }
        _schedule(job);
      }, delay);
    } else {
      _schedule(job);
    }
  }
}

let _visibilityBound = false;
function _bindVisibility() {
  if (_visibilityBound) return;
  _visibilityBound = true;
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') _pauseAll();
    else _resumeAll();
  });
}

export const RefreshScheduler = {
  /**
   * Đăng ký (hoặc thay thế) một periodic refresh job.
   * Gọi ngay 1 lần đầu KHÔNG được thực hiện — module tự load lần đầu.
   */
  register(name, cb, intervalMs) {
    _bindVisibility();
    if (_jobs.has(name)) this.unregister(name);
    const job = { cb, intervalMs, timerId: null, lastRunAt: null };
    _jobs.set(name, job);
    _schedule(job);
  },

  unregister(name) {
    const job = _jobs.get(name);
    if (!job) return;
    _clearTimer(job);
    _jobs.delete(name);
  },

  /** Số job đang quản lý (dùng cho debug). */
  size() { return _jobs.size; },
};
