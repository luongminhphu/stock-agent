/**
 * app.js — Entry point (Wave 7 + Wave 2b watchlist + Wave 5 decisions + Wave A leaderboard + Wave D lesson loop + Wave E brief ticker + Wave F brief feedback + Wave G brief generate + Wave 1 UX + Wave 2 memory + AttentionPanel + Wave 1 wire + Wave 2 wire + Wave 3 wire + Wave 4 wire + Wave A gap-wire + market breadth + engine heartbeat + today-loop)
 * Responsibility: import tất cả modules, wire events, khởi động dashboard.
 * Rule: KHÔNG chứa business logic. Chỉ bootstrap + wiring.
 */

import { el, openModal, closeModal } from './utils/dom.js';
import { loadDashboard, loadBacktesting } from './modules/dashboard/dashboard-loader.js';
import { loadThesisDetail }     from './modules/thesis/thesis-service.js';
import { bindLessonPersistedEvent } from './modules/thesis/thesis-service.js';
import {
  loadTheses,
  bindThesisActions as _bindThesisActions,
} from './modules/thesis/thesis-loader.js';
import { bindSuggestEvents }    from './modules/thesis/thesis-suggest.js';
import { loadPortfolio }        from './modules/portfolio/portfolio-loader.js';
import { loadWatchlist, handleAddTicker } from './modules/watchlist/watchlist-loader.js';
import {
  loadDecisions,
  loadLessons,
} from './modules/decision/decision-loader.js';
import { loadLeaderboard }      from './modules/leaderboard/leaderboard-service.js';
import { bindFeedbackEvents }   from './modules/briefing/brief-feedback.js';
import { bindGenerateBriefButtons } from './modules/briefing/brief-generate.js';
import { loadMemory }           from './modules/memory/memory-loader.js';
import { loadAttentionPanel, startAttentionAutoRefresh } from './modules/attention/attention-loader.js';
import { loadMarketBreadth }    from './modules/market/breadth.js';
import { debounce }             from './utils/debounce.js';
import { state }                from './state/dashboard-state.js';
import { loadTodayLoop, startTodayLoopAutoRefresh } from './modules/today-loop/today-loop-loader.js';
