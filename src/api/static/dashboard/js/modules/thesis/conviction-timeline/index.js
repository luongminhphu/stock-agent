/**
 * index.js — conviction-timeline public API
 * Owner: modules/thesis/conviction-timeline
 *
 * Re-exports tất cả public symbols để các module bên ngoài chỉ cần import từ
 * một entry point duy nhất:
 *
 *   import { loadConvictionTimeline, convictionTimelineSlotHTML,
 *            loadSparkChart, destroySpark, renderSparkChart }
 *     from './conviction-timeline/index.js';
 *
 * Backward-compatible: thay thế hoàn toàn cho import từ ./render-conviction-timeline.js
 */

export {
  convictionTimelineSlotHTML,
  renderConvictionTimeline,
  loadConvictionTimeline,
  parsePoints,
} from './renderer.js';

export {
  destroySpark,
  renderSparkChart,
  loadSparkChart,
} from './spark.js';

export {
  TIER,
  TREND_META,
  BD_META,
  VERDICT_CLS,
  EVENT_KIND_ICON,
  tierColor,
} from './constants.js';

export {
  ensureChartJs,
  buildDualChart,
  buildDualAnnotations,
  destroyCharts,
  hexToRgba,
  cssVar,
} from './chart-utils.js';
