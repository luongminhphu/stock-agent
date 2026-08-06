/**
 * index.js — conviction-timeline public API
 * Owner: modules/thesis/conviction-timeline
 *
 * Re-exports tất cả public symbols để các module bên ngoài chỉ cần import từ
 * một entry point duy nhất:
 *
 *   import { loadConvictionTimeline, convictionTimelineSlotHTML,
 *            loadSparkChart, destroySpark, renderSparkChart }
 *     from './conviction-timeline/index.js?v=1';
 *
 * Backward-compatible: thay thế hoàn toàn cho import từ ./render-conviction-timeline.js
 */

export {
  convictionTimelineSlotHTML,
  renderConvictionTimeline,
  loadConvictionTimeline,
  parsePoints,
} from './renderer.js?v=1';

export {
  destroySpark,
  renderSparkChart,
  loadSparkChart,
} from './spark.js?v=1';

export {
  TIER,
  TREND_META,
  BD_META,
  VERDICT_CLS,
  EVENT_KIND_ICON,
  tierColor,
} from './constants.js?v=1';

export {
  ensureChartJs,
  buildDualChart,
  buildDualAnnotations,
  destroyCharts,
  hexToRgba,
  cssVar,
} from './chart-utils.js?v=1';
