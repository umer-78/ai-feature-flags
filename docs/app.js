import { $, bars, esc, fail, int, kpis, legend, load, pct, seg, select, table, xy } from './kit.js';

// The canary from flags/canary.py, in the browser: same test, stages, check interval and minimum sample.
const TAU = 0.05, MIN_N = 20, ALPHA = 0.05, CHECK = 50, STAGES = [1, 5, 25, 50], PER_STAGE = 20000, POINT = 400, FLOOR = -4;

function erfc(x) {   // Numerical Recipes' erfcc: fractional error under 1.2e-7, tails included
  const z = Math.abs(x), t = 1 / (1 + 0.5 * z);
  const r = t * Math.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))));
  return x >= 0 ? r : 2 - r;
}
const logNdtr = (z) => (z < -20 ? -0.5 * z * z - Math.log(-z) - 0.5 * Math.log(2 * Math.PI) + Math.log(1 - 1 / (z * z)) : Math.log(0.5 * erfc(-z / Math.SQRT2)));

function evidence([[n0, s0, q0], [n1, s1, q1]], tol) {
  if (n0 < MIN_N || n1 < MIN_N) return { llr: -Infinity, d: 0 };
  const mean = (s0 + s1) / (n0 + n1), v = ((q0 + q1) / (n0 + n1) - mean * mean) * (1 / n0 + 1 / n1);
  if (!(v > 1e-12)) return { llr: -Infinity, d: 0 };
  const d = s1 / n1 - s0 / n0, t2 = TAU * TAU, x = d + tol;
  return { llr: 0.5 * Math.log(v / (v + t2)) + (t2 * x * x) / (2 * v * (v + t2)) + Math.LN2 + logNdtr((-x * TAU) / Math.sqrt(v * (v + t2))), d };
}

function mulberry32(a) {
  return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

function rollout(pair, segments, tol, seed) {
  const combos = [];
  pair.tasks.forEach((t, i) => { for (const [k, b, c] of [['both', 1, 1], ['broke', 1, 0], ['fixed', 0, 1], ['neither', 0, 0]]) if (t[k]) combos.push({ i, b, c, w: t[k] }); });
  let acc = 0;
  const cum = combos.map((x) => (acc += x.w));
  const names = ['overall', ...(segments ? pair.tasks.map((t) => t.task) : [])];
  const counts = names.map(() => [[0, 0, 0], [0, 0, 0]]);
  const rnd = mulberry32(seed), thr = Math.log(names.length / ALPHA);
  const s = { names, thr, seen: 0, stage: 0, status: 'running', served: 0, extra: 0, reason: '', history: names.map(() => []), at: null };
  s.step = (n) => {
    for (let k = 0; k < n && s.status === 'running'; k++) {
      const r = rnd() * acc;
      let lo = 0, hi = cum.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (cum[mid] > r) hi = mid; else lo = mid + 1; }
      const q = combos[lo], arm = rnd() * 10000 < STAGES[s.stage] * 100 ? 1 : 0, score = arm ? q.c : q.b;
      for (const g of segments ? [0, 1 + q.i] : [0]) { const c = counts[g][arm]; c[0] += 1; c[1] += score; c[2] += score * score; }
      if (arm) { s.served += 1; s.extra += q.b - q.c; }
      s.seen += 1;
      if (s.seen % CHECK === 0) {
        const ev = counts.map((c) => evidence(c, tol));
        if (s.seen % POINT === 0) ev.forEach((e, g) => s.history[g].push({ x: s.seen, y: Math.max(FLOOR, Math.min(thr + 4, e.llr)) }));
        const hit = ev.map((e, g) => [names[g], e]).filter(([, e]) => e.llr >= thr);
        if (hit.length) {
          s.status = 'rolled_back';
          s.at = s.seen;
          s.reason = 'on ' + hit.map(([g, e]) => `${g} (observed ${(100 * e.d).toFixed(1)} points)`).join(', ');
          ev.forEach((e, g) => s.history[g].push({ x: s.seen, y: Math.max(FLOOR, Math.min(thr + 4, e.llr)) }));
        }
      }
      if (s.status === 'running' && s.seen === (s.stage + 1) * PER_STAGE) {
        s.stage += 1;
        if (s.stage === STAGES.length) s.status = 'promoted';
      }
    }
  };
  return s;
}

try {
  const { summary, pairs } = await load();
  const aa = Object.fromEntries(summary.identical.map((r) => [`${r.method}/${r.guardrails}`, r]));
  const bad = summary.upgrades.find((u) => u.candidate === 'gemini-1.5-flash-002' && u.guardrails === 'segments');
  kpis($('#kpis'), [
    { label: 'False rollbacks, this test', value: pct(aa['always_valid/overall'].false_rollbacks / aa['always_valid/overall'].runs), note: 'identical candidate, 1,200 rollouts; target under 5%' },
    { label: 'Same, re-checked fixed test', value: pct(aa['naive/overall'].false_rollbacks / aa['naive/overall'].runs), note: 'a z-test re-run every 50 requests' },
    { label: 'Worst upgrade caught', value: `${bad.rolled_back}/${bad.runs}`, note: `Gemini 1.5 Flash 002, after a median ${int(bad.median_served)} candidate requests` },
    { label: 'Upgrades replayed', value: String(pairs.length), note: '100 rollouts each, 80,000 requests' },
  ]);

  let pair = pairs[2], segments = true, tol = 0.02, seed = 1, sim = null, raf = 0;
  const colors = ['var(--text)', 'var(--c2)', 'var(--c3)', 'var(--c4)', 'var(--c5)', 'var(--accent)'];
  const direct = (p) => { const t = p.tasks.reduce((a, x) => ({ n: a.n + x.both + x.broke + x.fixed + x.neither, e: a.e + x.broke - x.fixed }), { n: 0, e: 0 }); return (80000 * t.e) / t.n; };

  function paint() {
    const s = sim, done = s.status !== 'running';
    $('#stages').innerHTML = [...STAGES, 100].map((p, i) => `<span class="step${(s.status === 'promoted' ? i === STAGES.length : i === s.stage && s.status === 'running') ? ' on' : ''}">${p}%</span>`).join('<span class="muted">→</span>') +
      (s.status === 'rolled_back' ? ' <span class="pill no">rolled back at stage ' + STAGES[s.stage] + '%</span>' : s.status === 'promoted' ? ' <span class="pill ok">promoted to everyone</span>' : '');
    const series = s.names.map((n, g) => ({ name: n, color: colors[g % colors.length], width: g ? 1.6 : 2.6, points: s.history[g].length ? s.history[g] : [{ x: 0, y: FLOOR }] }));
    xy($('#ev'), {
      label: 'Evidence that the candidate is worse, by requests', height: 260, series, marginRight: 20,
      x: { min: 0, max: Math.min(STAGES.length, s.stage + 1) * PER_STAGE, fmt: (v) => (v ? `${v / 1000}k` : '0'), label: 'requests' },
      y: { min: FLOOR, max: s.thr + 4, fmt: (v) => v.toFixed(0), label: 'log likelihood ratio' },
      hline: { y: s.thr, label: `roll back (log ${s.names.length}/α)` },
      vline: s.at ? { x: s.at, label: `rolled back at ${int(s.at)}` } : null,
    });
    legend($('#evKey'), series);
    $('#out').innerHTML = `<div>Requests so far<b>${int(s.seen)}</b><span class="muted small">${done ? (s.status === 'promoted' ? 'all four stages passed' : esc(s.reason)) : `stage ${STAGES[s.stage]}%`}</span></div>` +
      `<div>Served by the candidate<b>${int(s.served)}</b><span class="muted small">requests that reached the new version</span></div>` +
      `<div>Extra wrong answers<b>${int(s.extra)}</b><span class="muted small">switching everyone at once: ${int(direct(pair))} over 80,000</span></div>`;
  }

  function start() {
    cancelAnimationFrame(raf);
    sim = rollout(pair, segments, tol, seed);
    const tick = () => { sim.step(1200); paint(); if (sim.status === 'running') raf = requestAnimationFrame(tick); };
    tick();
  }

  select($('#pair'), pairs.map((p, i) => [i, `${p.base} → ${p.candidate}`]), 2, (i) => { pair = pairs[+i]; if (sim) start(); });
  seg($('#guards'), [['1', 'overall + each task'], ['0', 'overall only']], '1', (v) => { segments = v === '1'; if (sim) start(); });
  seg($('#tol'), [['0', '0'], ['0.01', '1 pt'], ['0.02', '2 pts'], ['0.05', '5 pts']], '0.02', (v) => { tol = +v; if (sim) start(); });
  $('#run').onclick = () => { seed += 1; start(); };
  start();

  table($('#bench'), [
    { key: 'name', label: 'Upgrade' },
    { key: 'guardrails', label: 'Guardrails' },
    { key: 'rb', label: 'Rolled back', num: true },
    { key: 'when', label: 'Median rollback' },
    { key: 'served', label: 'Candidate requests', num: true },
    { key: 'extra', label: 'Extra wrong', num: true },
    { key: 'direct', label: 'Direct switch', num: true },
  ], summary.upgrades.map((u) => ({
    name: `${u.base} → ${u.candidate}`, guardrails: u.guardrails, rb: `${u.rolled_back}/${u.runs}`,
    when: u.median_rollback_request ? `request ${int(u.median_rollback_request)} (${u.median_rollback_stage}% stage)` : '–',
    served: int(u.median_served), extra: int(u.median_extra_wrong), direct: int(u.direct_switch_extra_wrong),
  })), { cls: (r) => (r.rb.startsWith('0/') ? '' : 'bad') });

  bars($('#aa'), summary.identical.map((r) => ({
    label: `${r.method === 'naive' ? 'fixed test, re-checked' : 'always-valid'} · ${r.guardrails}`, value: r.false_rollbacks / r.runs,
    text: `${pct(r.false_rollbacks / r.runs)} (${r.false_rollbacks}/${r.runs})`, color: r.method === 'naive' ? 'var(--bad)' : 'var(--accent)',
  })), { max: 0.6 });
} catch (err) {
  fail(err);
}
