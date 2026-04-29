# dashboard/modules/

Mỗi subdirectory = 1 business concern. Không import chéo giữa các sibling module ngoài trừ qua `state/` hoặc `utils/`.

```
modules/
├── thesis/
│   ├── render-thesis-table.js   — HTML render: table, detail, assump/cat items
│   ├── render-score.js          — Score breakdown bar
│   ├── render-ai-review.js      — AI review section + result
│   ├── thesis-form.js           — Form lifecycle: rows, collect, sync, open modals
│   ├── thesis-service.js        — API calls: load detail, triggerAiReview, openApplyAiReviewModal
│   ├── thesis-suggest.js        — AI suggest: apply to form, render suggest results
│   └── wire-detail-actions.js   — Event wiring cho detail panel (adapter mỏng)
│
├── briefing/
│   └── render-brief.js          — renderBriefCard, renderVerdicts, renderSnapshots
│
├── backtesting/
│   └── render-backtesting.js    — renderAccuracy, renderPerformance
│
├── scan/
│   └── render-scan.js           — scan result render
│
└── dashboard/
    └── dashboard-loader.js      — loadDashboard (orchestrator), renderSummary, renderCatalystList
```

## Quy tắc import
- `utils/` → có thể import ở mọi nơi
- `state/` → có thể import ở mọi nơi
- `api/`   → có thể import ở mọi nơi
- Module A không import module B cùng cấp (tránh circular), trừ khi rõ ràng là dependency tree
