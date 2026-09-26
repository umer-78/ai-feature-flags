import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from flags.canary import Canary
from flags.core import BUCKETS, Flag, Platform, bucket
from flags.replay import Pool, fast, live, user_buckets
from flags.server import make_server

SMALL = {"stages": [1, 5, 25, 50], "stage_requests": 4000}


def synthetic(base_rates, cand_rates, per_task=500, seed=0):
    rng = np.random.default_rng(seed)
    task = np.repeat(np.arange(len(base_rates)), per_task)
    draw = lambda rates: (rng.random(task.size) < np.array(rates)[task]).astype(float)
    return Pool([f"t{i}" for i in range(len(base_rates))], task, draw(base_rates), draw(cand_rates))


def identical(pool):
    return Pool(pool.tasks, pool.task, pool.base, pool.base.copy())


def rollbacks(pool, buckets, runs, **settings):
    return np.mean([fast(pool, Canary(**SMALL, **settings), s, buckets)["state"] == "rolled_back" for s in range(runs)])


@pytest.fixture(scope="module")
def buckets():
    return user_buckets()


def model_flag(**kw):
    return Flag("m", {"control": {"model": "a"}, "candidate": {"model": "b"}}, "control", "candidate", **kw)


def test_bucketing_is_sticky_even_and_only_moves_users_forward(buckets):
    assert bucket("f", "alice") == bucket("f", "alice") and 0 <= bucket("f", "alice") < BUCKETS
    assert abs((buckets < BUCKETS // 10).mean() - 0.10) < 0.01
    flag, on = model_flag(), []
    for stage in range(len(flag.canary.stages)):
        flag.canary.stage = stage
        on.append({u for u in range(3000) if flag.evaluate(f"u{u}")[0] == "candidate"})
    assert all(a <= b for a, b in zip(on, on[1:])) and 0 < len(on[0]) < len(on[-1])


def test_rules_kill_switch_and_which_scores_count():
    flag = model_flag(rules=[{"if": {"user": ["qa"]}, "serve": "candidate"}, {"if": {"plan": "enterprise"}, "serve": "control"}])
    assert flag.evaluate("qa") == ("candidate", "rule 0")
    assert flag.evaluate("x", {"plan": "enterprise"}) == ("control", "rule 1")
    p = Platform([flag])
    p.score("m", "qa", "candidate", 1)                  # decided by a rule, not the rollout: not counted
    assert p.status("m")["scored"] == 0
    p.score("m", "someone", "control", 1)
    assert p.status("m")["scored"] == 1
    with pytest.raises(ValueError):
        p.score("m", "someone", "control", 3)
    p.set_enabled("m", False)
    assert flag.evaluate("qa") == ("control", "flag off")
    p.score("m", "someone", "control", 1)               # off: not counted
    assert p.status("m")["scored"] == 1


def test_always_valid_keeps_false_rollbacks_down_where_re_testing_does_not(buckets):
    pool = identical(synthetic([0.8, 0.6, 0.4], [0.8, 0.6, 0.4]))
    valid = rollbacks(pool, buckets, 200, tolerance=0.0)
    naive = rollbacks(pool, buckets, 200, method="naive", tolerance=0.0)
    assert valid <= 0.05 and naive >= 0.15


def test_rolls_back_a_real_regression_early(buckets):
    pool = synthetic([0.75] * 3, [0.62] * 3)
    rs = [fast(pool, Canary(**SMALL), s, buckets) for s in range(50)]
    assert all(r["state"] == "rolled_back" for r in rs)
    assert np.median([r["requests"] for r in rs]) < 3 * SMALL["stage_requests"]


def test_segment_guardrail_catches_what_the_average_hides(buckets):
    pool = synthetic([0.7] * 4, [0.75, 0.75, 0.75, 0.55])
    assert abs(pool.cand.mean() - pool.base.mean()) < 0.02
    assert rollbacks(pool, buckets, 50) <= 0.1
    rs = [fast(pool, Canary(**SMALL, segments=pool.tasks), s, buckets) for s in range(50)]
    assert np.mean([r["fired"] == ["t3"] for r in rs]) >= 0.9


def test_vectorized_replay_matches_request_by_request(buckets):
    keys = ("state", "requests", "stage", "served", "extra_wrong", "fired")
    for pool in (synthetic([0.8, 0.6], [0.8, 0.6]), synthetic([0.8, 0.6], [0.72, 0.45])):
        for seed in range(3):
            for segments in ([], pool.tasks):
                a = fast(pool, Canary(**SMALL, segments=segments), seed, buckets)
                b = live(pool, Canary(**SMALL, segments=segments), seed)
                assert {k: a[k] for k in keys} == {k: b[k] for k in keys}


def test_state_survives_a_restart(tmp_path):
    config = tmp_path / "flags.yaml"
    config.write_text("flags:\n  m:\n    variants: {control: {model: a}, candidate: {model: b}}\n"
                      "    control: control\n    candidate: candidate\n    rollout: {segments: [billing]}\n")
    p = Platform.load(config, tmp_path / "state.json")
    for i in range(100):
        p.score("m", f"u{i}", "control", i % 2, "billing")
    p.set_enabled("m", False)
    q = Platform.load(config, tmp_path / "state.json")
    assert q.status("m") == p.status("m") and not q.flags["m"].enabled
    assert np.array_equal(q.flags["m"].canary.counts, p.flags["m"].canary.counts)


def test_http_api():
    server = make_server(Platform([model_flag()]), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base, opener = f"http://127.0.0.1:{server.server_address[1]}", urllib.request.build_opener(urllib.request.ProxyHandler({}))
    get = lambda path: json.loads(opener.open(base + path).read())
    post = lambda path, body: json.loads(opener.open(urllib.request.Request(
        base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})).read())
    try:
        served = get("/flags/m?user=alice&plan=free")
        assert served["variant"] in ("control", "candidate") and served["config"]["model"] in ("a", "b")
        assert post("/flags/m/score", {"user": "alice", "variant": served["variant"], "score": 1})["scored"] == 1
        assert get("/flags/m/status")["state"] == "running"
        post("/flags/m/enabled", {"enabled": False})
        assert get("/flags/m?user=alice")["variant"] == "control"
        for path in ("/flags/nope?user=a", "/flags/m"):
            with pytest.raises(urllib.error.HTTPError):
                get(path)
    finally:
        server.shutdown()
