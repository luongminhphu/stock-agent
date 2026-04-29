// Wave 2: Centralized mutable state — không dùng global let rải rác
export const state = {
  selectedThesisId: null,
  theses: [],
  deleteCallback: null,
  latestAiReviews: {},
  aiApplyThesisId: null,
  aiSelectedRecIds: [],
};

export function resetAiApply() {
  state.aiApplyThesisId = null;
  state.aiSelectedRecIds = [];
}
