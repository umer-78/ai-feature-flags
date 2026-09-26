"""Replaying a rollout against recorded answers.

HELM Lite recorded both versions' answers to the same questions, so for any request we know
whether each version would have got it right. A replay sends a stream of requests (questions
drawn uniformly from those both versions answered, users from a fixed population), lets the
flag assign each one, scores it with the recorded outcome of the version that user got, and
feeds the score to the canary. It measures the decision, when it came, and how many more
wrong answers users saw than control would have given them.
"""
import functools
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import data
from .canary import Canary
from .core import BUCKETS, Flag, bucket

USERS = 20_000
FLAG = "answer-model"
ROLLOUT = {"stages": [1, 5, 25, 50], "stage_requests": 20_000}
RESULTS = Path(__file__).resolve().parent.parent / "results"


@dataclass
class Pool:
    tasks: list          # segment names
    task: np.ndarray     # per question, index into tasks
    base: np.ndarray     # 1 if the control version got it right
    cand: np.ndarray     # 1 if the candidate did


_load = functools.lru_cache(None)(data.load)


def load_pool(base, candidate):
    b, c = _load(base), _load(candidate)
    keys = sorted(set(b) & set(c))
    tasks = sorted({b[k]["task"] for k in keys})
    return Pool(tasks, np.array([tasks.index(b[k]["task"]) for k in keys]),
                np.array([b[k]["correct"] for k in keys], float), np.array([c[k]["correct"] for k in keys], float))


def user_buckets():
    return np.array([bucket(FLAG, f"u{i}") for i in range(USERS)])


def traffic(pool, canary, seed):
    """(question, user) for every request the rollout could see."""
    rng = np.random.default_rng(seed)
    n = len(canary.stages) * canary.stage_requests
    return rng.integers(len(pool.base), size=n), rng.integers(USERS, size=n)


def live(pool, canary, seed):
    """One request at a time through Flag.evaluate and Canary.record, as in production."""
    flag = Flag(FLAG, {"control": {}, "candidate": {}}, "control", "candidate", canary=canary)
    served = extra = 0
    for q, u in zip(*traffic(pool, canary, seed)):
        arm = int(flag.evaluate(f"u{u}")[0] == "candidate")
        served += arm
        extra += arm * (pool.base[q] - pool.cand[q])
        if canary.record(arm, (pool.base, pool.cand)[arm][q], pool.tasks[pool.task[q]]) != "running":
            break
    hit, _ = canary.fired(canary.counts)
    return {"state": canary.state, "requests": canary.seen, "stage": canary.percent() or canary.stages[canary.stage],
            "served": served, "extra_wrong": int(extra),
            "fired": [g for g, h in zip(canary.guardrails, hit) if h] if canary.state == "rolled_back" else []}


def fast(pool, canary, seed, buckets):
    """The same replay, vectorized for the bench; `canary` only supplies settings. Its decisions
    match `live` exactly (tests/test_flags.py checks)."""
    q, u = traffic(pool, canary, seed)
    n, every = len(q), canary.check_every
    stage = np.arange(n) // canary.stage_requests
    arm = (buckets[u] < np.round(np.array(canary.stages)[stage] * BUCKETS / 100)).astype(int)
    score = np.where(arm == 1, pool.cand[q], pool.base[q])
    guard = np.array([1 + canary.segments.index(t) if t in canary.segments else -1 for t in pool.tasks])[pool.task[q]]
    blocks, g_count = n // every, 1 + len(canary.segments)
    m = blocks * every
    blk, a, s, g = np.arange(m) // every, arm[:m], score[:m], guard[:m]
    counts = np.zeros((blocks, g_count, 2, 3))
    for rows, keep in ((np.zeros(m, int), np.ones(m, bool)), (g, g >= 0)):
        idx = (blk[keep] * g_count + rows[keep]) * 2 + a[keep]
        for k, w in enumerate((np.ones(m), s, s * s)):
            counts[..., k] += np.bincount(idx, w[keep], blocks * g_count * 2).reshape(blocks, g_count, 2)
    hit, _ = canary.fired(counts.cumsum(0))
    first = np.flatnonzero(hit.any(1))
    end = (first[0] + 1) * every if first.size else n
    on = arm[:end] == 1
    names = ["overall"] + list(canary.segments)
    return {"state": "rolled_back" if first.size else "promoted", "requests": int(end),
            "stage": int(canary.stages[stage[end - 1]]) if first.size else 100, "served": int(on.sum()),
            "extra_wrong": int((pool.base[q[:end]] - pool.cand[q[:end]])[on].sum()),
            "fired": [names[i] for i in np.flatnonzero(hit[first[0]])] if first.size else []}


def wilson(k, n, z=1.96):
    p, d = k / n, 1 + z * z / n
    mid, half = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, mid - half), min(1.0, mid + half)


def bench(runs=100):
    cfg, buckets = data.config(), user_buckets()
    horizon = len(ROLLOUT["stages"]) * ROLLOUT["stage_requests"]
    pct = lambda x: f"{100 * x:.1f}%"
    upgrades, lines = [], ["## Upgrades", "",
        f"{runs} replays each, {horizon:,} requests, candidate on {'/'.join(map(str, ROLLOUT['stages']))}% of users "
        f"for {ROLLOUT['stage_requests']:,} requests per stage. Extra wrong answers: served by the candidate beyond "
        "what control would have got wrong (median over replays); a direct switch is every request on the candidate. "
        f"Tolerance {100 * Canary.tolerance:g} points.", "",
        "| Upgrade | Overall | Guardrails | Rolled back | Median rollback | Candidate requests (median) | Extra wrong answers | Direct switch |",
        "|---|---|---|---|---|---|---|---|"]
    for base, cand in cfg["pairs"]:
        pool = load_pool(base, cand)
        for guards in ("overall", "segments"):
            canary = Canary(**ROLLOUT, segments=pool.tasks if guards == "segments" else [])
            rs = [fast(pool, canary, seed, buckets) for seed in range(runs)]
            back = [r for r in rs if r["state"] == "rolled_back"]
            fired = sorted({f for r in back for f in r["fired"]})
            row = {"base": base, "candidate": cand, "guardrails": guards, "control": float(pool.base.mean()),
                   "candidate_score": float(pool.cand.mean()), "rolled_back": len(back), "runs": runs, "fired": fired,
                   "median_rollback_request": float(np.median([r["requests"] for r in back])) if back else None,
                   # the stage the median rollback request falls in (a median of stage numbers means nothing)
                   "median_rollback_stage": ROLLOUT["stages"][min(int(np.median([r["requests"] for r in back]) - 1) // ROLLOUT["stage_requests"], len(ROLLOUT["stages"]) - 1)] if back else None,
                   "median_served": float(np.median([r["served"] for r in rs])),
                   "median_extra_wrong": float(np.median([r["extra_wrong"] for r in rs])),
                   "direct_switch_extra_wrong": float(horizon * (pool.base.mean() - pool.cand.mean()))}
            upgrades.append(row)
            when = (f"request {row['median_rollback_request']:,.0f} ({row['median_rollback_stage']:g}% stage)" if back else "—")
            lines.append(f"| {base} → {cand} | {pct(row['control'])} → {pct(row['candidate_score'])} | {guards} | "
                         f"{len(back)}/{runs}{' (' + ', '.join(fired) + ')' if fired else ''} | {when} | "
                         f"{row['median_served']:,.0f} | {row['median_extra_wrong']:,.0f} | {row['direct_switch_extra_wrong']:,.0f} |")
    aa, versions = [], list(cfg["versions"])
    lines += ["", "## Identical candidate (A/A): false rollbacks", "",
              f"Each of the {len(versions)} versions against itself, {runs} replays each; every rollback is a false alarm. "
              f"Tolerance 0, so the candidate sits exactly on the line the test guards, the hardest case. Target: under {pct(Canary.alpha)}.", "",
              "| Test | Guardrails | False rollbacks | 95% interval |", "|---|---|---|---|"]
    pools = [load_pool(v, v) for v in versions]
    for method, label in (("always_valid", "always-valid (this project)"), ("naive", "fixed-sample test re-run every 50 requests")):
        for guards in ("overall", "segments"):
            k = sum(fast(p, Canary(**ROLLOUT, method=method, tolerance=0.0, segments=p.tasks if guards == "segments" else []),
                         10_000 + seed, buckets)["state"] == "rolled_back" for p in pools for seed in range(runs))
            n = len(pools) * runs
            lo, hi = wilson(k, n)
            aa.append({"method": method, "guardrails": guards, "false_rollbacks": k, "runs": n})
            lines.append(f"| {label} | {guards} | {k}/{n} ({pct(k / n)}) | {pct(lo)}–{pct(hi)} |")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "bench.md").write_text("\n".join(lines) + "\n")
    (RESULTS / "summary.json").write_text(json.dumps({"rollout": ROLLOUT, "upgrades": upgrades, "identical": aa}, indent=1))
    return "\n".join(lines)
