function scoreBar(s) {
  if (s == null) return '<span class="score-empty">—</span>';
  const n = Math.max(0, Math.min(Number(s), 100));
  const cls = n >= 70 ? 'score-high' : n >= 40 ? 'score-mid' : 'score-low';
  return `
    <div class="score-visual">
      <div class="score-track"><div class="score-fill ${cls}" style="width:${n}%"></div></div>
      <span class="score-number ${cls}">${n}</span>
    </div>`;
}
