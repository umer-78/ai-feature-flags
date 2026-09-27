"""python -m flags.demo   write the live demo's data (docs/data.json): results/summary.json, plus, for every
upgrade, how many questions of each task each version got right. That joint count is all a replay
draws from, so the page's in-browser rollout samples the same outcomes as the bench."""
import json
from pathlib import Path

from . import data
from .replay import load_pool

ROOT = Path(__file__).resolve().parent.parent


def build(out=ROOT / "docs"):
    summary = json.loads((ROOT / "results" / "summary.json").read_text())
    pairs = []
    for base, cand in data.config()["pairs"]:
        pool = load_pool(base, cand)
        tasks = []
        for i, name in enumerate(pool.tasks):
            m = pool.task == i
            b, c = pool.base[m], pool.cand[m]
            tasks.append({"task": name, "both": int(((b == 1) & (c == 1)).sum()), "broke": int(((b == 1) & (c == 0)).sum()),
                          "fixed": int(((b == 0) & (c == 1)).sum()), "neither": int(((b == 0) & (c == 0)).sum())})
        pairs.append({"base": base, "candidate": cand, "tasks": tasks})
    out.mkdir(exist_ok=True)
    (out / "data.json").write_text(json.dumps({"summary": summary, "pairs": pairs}, indent=1))
    print(f"wrote {out / 'data.json'}: {len(pairs)} upgrades")


if __name__ == "__main__":
    build()
