// Wave 2: Centralized mutable state — không dùng global let rải rác
export const state = {
  selectedThesisId: null,
  theses: [],
  deleteCallback: null,
  latestAiReviews: {},
  aiApplyThesisId: null,
  aiSelectedRecIds: [],

  // Wave 2 PERF: backtesting cache metadata
  cachedVerdictAccuracy: null,
  cachedVerdictAccuracyAt: 0,
};

export function resetAiApply() {
  state.aiApplyThesisId = null;
  state.aiSelectedRecIds = [];
}
