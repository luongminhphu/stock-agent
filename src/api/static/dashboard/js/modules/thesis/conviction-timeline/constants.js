/**
 * constants.js
 * Owner: modules/thesis/conviction-timeline
 * Responsibility: Static config — tier zones, verdict/event/trend metadata.
 */

export const TIER = [
  { min: 0,  max: 30,  label: 'Critical', color: '#d163a7' },
  { min: 30, max: 50,  label: 'Weak',     color: '#fdab43' },
  { min: 50, max: 65,  label: 'Moderate', color: '#e8af34' },
  { min: 65, max: 80,  label: 'Healthy',  color: '#6daa45' },
  { min: 80, max: 100, label: 'Strong',   color: '#4f98a3' },
];

export const TREND_META = {
  improving:         { icon: '↑', label: 'Improving',        cls: 'cv-trend--up' },
  declining:         { icon: '↓', label: 'Declining',        cls: 'cv-trend--down' },
  stable:            { icon: '→', label: 'Stable',           cls: 'cv-trend--stable' },
  insufficient_data: { icon: '—', label: 'Insufficient data', cls: '' },
};

export const BD_META = [
  { key: 'assumption_health', label: 'Assumption Health', color: '#6daa45' },
  { key: 'catalyst_progress', label: 'Catalyst Progress', color: '#4f98a3' },
  { key: 'risk_reward',       label: 'Risk / Reward',     color: '#e8af34' },
  { key: 'review_confidence', label: 'AI Confidence',     color: '#d163a7' },
];

export const VERDICT_CLS = {
  BUY:      'cv-vtag--buy',
  HOLD:     'cv-vtag--hold',
  REDUCE:   'cv-vtag--reduce',
  SELL:     'cv-vtag--sell',
  BULLISH:  'cv-vtag--buy',
  BEARISH:  'cv-vtag--sell',
  NEUTRAL:  'cv-vtag--hold',
  WATCHLIST:'cv-vtag--hold',
};

export const EVENT_KIND_ICON = {
  reviewed: '🤖',
  snapshot: '📸',
  created:  '🔬',
  updated:  '✏️',
};

/** Map conviction score → tier color. */
export function tierColor(score) {
  const s = Number(score);
  if (s >= 80) return '#4f98a3';
  if (s >= 65) return '#6daa45';
  if (s >= 50) return '#e8af34';
  if (s >= 30) return '#fdab43';
  return '#d163a7';
}
