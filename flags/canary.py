"""The canary: move a flag's candidate from a few users to everyone, and back to control the
moment it is measurably worse.

Each scored request lands in an arm, control or candidate. The comparison is an always-valid
sequential test, a mixture likelihood ratio in the style of Johari et al., "Peeking at A/B
Tests" (2017). It can be checked after every batch of requests, through every stage, for as
long as the rollout runs, and the chance it ever fires while the candidate is at least as good
as control stays under alpha (Ville's inequality). Re-running a fixed-sample test at every
check has no such guarantee; the bench measures how often that rolls back a candidate that is
identical to control.

The test is one-sided: it only gathers evidence that the candidate is worse than control by
more than `tolerance` (2 points by default, so a real but trivial drop does not block a
release). It uses the pooled variance, the right one while the arms are equal. It guards the overall score and each
segment named in `segments` (a product feature, a task, a customer tier), since a regression
confined to one segment can vanish in the average. Bonferroni across the guardrails keeps the
combined false-rollback rate under alpha.
"""
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.special import log_ndtr, ndtr

TAU = 0.05     # spread of the prior on the true difference: the test is quickest on drops near 5 points
MIN_N = 20     # scored requests each arm needs before a guardrail can fire


def _moments(counts):
    c = np.asarray(counts, dtype=float)
    n0, s0, q0, n1, s1, q1 = (c[..., a, k] for a in (0, 1) for k in (0, 1, 2))
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = (s0 + s1) / (n0 + n1)
        v = ((q0 + q1) / (n0 + n1) - mean ** 2) * (1 / n0 + 1 / n1)
        d = s1 / n1 - s0 / n0
    ok = (n0 >= MIN_N) & (n1 >= MIN_N) & (v > 1e-12)
    return np.where(ok, d, 0.0), np.where(ok, v, 1.0), ok


def evidence(counts, tau=TAU, tolerance=0.0):
    """log likelihood ratio for 'the candidate is worse by more than tolerance', and the observed difference
    (candidate minus control). counts[..., arm, stat]: arm 0 is control, 1 the candidate;
    stat is (requests, sum of scores, sum of squared scores). Any leading shape."""
    d, v, ok = _moments(counts)
    t2, x = tau * tau, d + tolerance
    llr = (0.5 * np.log(v / (v + t2)) + t2 * x * x / (2 * v * (v + t2))
           + math.log(2) + log_ndtr(-x * tau / np.sqrt(v * (v + t2))))
    return np.where(ok, llr, -np.inf), d


def naive_p(counts, tolerance=0.0):
    """One-sided p-value of a fixed-sample z-test, what re-checking a classic test gives. For comparison."""
    d, v, ok = _moments(counts)
    return np.where(ok, ndtr((d + tolerance) / np.sqrt(v)), 1.0), d


@dataclass
class Canary:
    stages: list = field(default_factory=lambda: [1, 5, 25, 50])   # percent of users on the candidate
    stage_requests: int = 20_000     # scored requests at each stage before the next; after the last, 100%
    segments: list = field(default_factory=list)                   # guarded besides the overall score
    alpha: float = 0.05
    tolerance: float = 0.02          # drops smaller than this (2 points) are not grounds to roll back
    check_every: int = 50
    method: str = "always_valid"     # or "naive": a fixed-sample test re-run at every check (bench only)
    state: str = "running"           # running, rolled_back or promoted
    stage: int = 0
    seen: int = 0
    reason: str = ""
    log: list = field(default_factory=list)
    counts: list = None              # [guardrail][arm][requests, sum, sum of squares]; guardrail 0 is overall

    def __post_init__(self):
        shape = (1 + len(self.segments), 2, 3)
        self.counts = np.zeros(shape) if self.counts is None else np.array(self.counts, dtype=float).reshape(shape)

    @property
    def guardrails(self):
        return ["overall"] + list(self.segments)

    def percent(self):
        if self.state == "running":
            return self.stages[self.stage]
        return 100 if self.state == "promoted" else 0

    def record(self, arm, score, segment=None):
        """One scored request: arm 0 control, 1 candidate; score from 0 to 1."""
        if self.state != "running":
            return self.state
        rows = [0] + ([1 + self.segments.index(segment)] if segment in self.segments else [])
        self.counts[rows, arm] += (1, score, score * score)
        self.seen += 1
        if self.seen % self.check_every == 0:
            self.check()
        if self.state == "running" and self.seen == (self.stage + 1) * self.stage_requests:
            self.stage += 1
            done = self.stage == len(self.stages)
            self.state = "promoted" if done else "running"
            self.log.append({"at": self.seen, "event": "promoted to 100%" if done else f"stage {self.stages[self.stage]}%"})
        return self.state

    def fired(self, counts):
        """Which guardrails fire on these counts (any leading shape), and the observed differences."""
        m = counts.shape[-3]
        if self.method == "naive":
            p, d = naive_p(counts, self.tolerance)
            return p < self.alpha / m, d
        llr, d = evidence(counts, tolerance=self.tolerance)
        return llr >= math.log(m / self.alpha), d

    def check(self):
        hit, d = self.fired(self.counts)
        if hit.any():
            self.state = "rolled_back"
            self.reason = ", ".join(f"{g} {100 * x:+.1f} points" for g, x, h in zip(self.guardrails, d, hit) if h)
            self.log.append({"at": self.seen, "event": f"rolled back: {self.reason}"})

    def to_dict(self):
        out = {k: getattr(self, k) for k in self.__dataclass_fields__}
        out["counts"] = self.counts.tolist()
        return out
