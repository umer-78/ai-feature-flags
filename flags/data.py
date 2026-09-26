"""Recorded answers for each model version, from HELM Lite's public results: for every
question, whether the version got it right, the answer it gave, and its token counts.
Downloaded on first use into FLAGS_DATA (default ~/.cache/flags)."""
import json
import os
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

BUCKET = "https://storage.googleapis.com/crfm-helm-public/lite/benchmark_output/runs"
LISTING = "https://storage.googleapis.com/storage/v1/b/crfm-helm-public/o"
VERSIONS = Path(__file__).with_name("versions.yaml")
TASKS = {"gsm": ("gsm8k", "final_number_exact_match"), "math": ("math", "math_equiv_chain_of_thought"),
         "mmlu": ("mmlu", "exact_match"), "med_qa": ("medqa", "exact_match"),
         "commonsense": ("openbookqa", "exact_match"), "legalbench": ("legalbench", "quasi_exact_match")}


def config():
    return yaml.safe_load(VERSIONS.read_text())


def cache_dir():
    path = Path(os.environ.get("FLAGS_DATA", Path.home() / ".cache" / "flags"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_json(url, path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as r:
            path.write_bytes(r.read())
    return json.loads(path.read_text())


def runs(helm, release):
    names, token = [], None
    while True:
        q = {"prefix": f"lite/benchmark_output/runs/{release}/", "delimiter": "/", "maxResults": 1000}
        if token:
            q["pageToken"] = token
        page = get_json(f"{LISTING}?{urllib.parse.urlencode(q)}", cache_dir() / "listing" / f"{release}-{token or 0}.json")
        names += [p.rstrip("/").rsplit("/", 1)[1] for p in page.get("prefixes", [])]
        token = page.get("nextPageToken")
        if not token:
            return [n for n in names if n.split(":")[0] in TASKS and re.search(rf"(?:^|[,:])model={re.escape(helm)}(?:,|$)", n)]


def answer_of(prediction):
    text = (prediction.get("predicted_text") or "").strip()
    return text[:200]


def load(name):
    """{question key: {"task", "subtask", "question", "correct", "answer", "output_tokens"}} for one version."""
    v = config()["versions"][name]
    out = {}
    def fetch(run):
        base, local = f"{BUCKET}/{v['release']}/{urllib.parse.quote(run, safe='')}", cache_dir() / v["release"] / urllib.parse.quote(run, safe="")
        return run, get_json(f"{base}/display_predictions.json", local / "p.json"), get_json(f"{base}/instances.json", local / "i.json")
    with ThreadPoolExecutor(8) as pool:
        for run, preds, instances in pool.map(fetch, runs(v["helm"], v["release"])):
            scenario = run.split(":")[0]
            task, metric = TASKS[scenario]
            args = dict(a.split("=", 1) for a in run.split(":", 1)[1].split(",") if "=" in a)
            sub = args.get("subject") or args.get("subset") or args.get("dataset") or ""
            text = {i["id"]: i["input"]["text"] for i in instances}
            for p in preds:
                if p.get("train_trial_index", 0) == 0 and metric in p["stats"]:
                    out[f"{task}/{sub}/{p['instance_id']}"] = {
                        "task": task, "subtask": sub, "question": text.get(p["instance_id"], ""),
                        "correct": int(p["stats"][metric] >= 1.0), "answer": answer_of(p),
                        "output_tokens": int(p["stats"].get("num_output_tokens", 0))}
    return out
