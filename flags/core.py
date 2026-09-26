"""Flags: which variant of an AI feature (a model, a prompt, its settings) a request gets.

Assignment is deterministic. A user's bucket is a hash of the flag name and user id, so a user
keeps the same variant on every request and every server, and raising the rollout only moves
users from control to the candidate, never back. Targeting rules come first (first match
wins); `enabled: false` is the kill switch and sends everyone to control.
"""
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .canary import Canary

BUCKETS = 10_000


def bucket(flag, user):
    return int.from_bytes(hashlib.sha256(f"{flag}:{user}".encode()).digest()[:8], "big") % BUCKETS


def matches(condition, user, attrs):
    """condition: {attribute: value or list of values}; the attribute `user` is the user id."""
    for key, want in condition.items():
        have = user if key == "user" else attrs.get(key)
        if have not in (want if isinstance(want, list) else [want]):
            return False
    return True


@dataclass
class Flag:
    name: str
    variants: dict                 # variant -> the config the application receives
    control: str
    candidate: str
    rules: list = field(default_factory=list)      # [{"if": condition, "serve": variant}]
    enabled: bool = True
    canary: Canary = field(default_factory=Canary)

    @classmethod
    def from_config(cls, name, cfg):
        return cls(name, cfg["variants"], cfg["control"], cfg["candidate"], cfg.get("rules", []),
                   cfg.get("enabled", True), Canary(**cfg.get("rollout", {})))

    def rule_for(self, user, attrs=None):
        return next((i for i, r in enumerate(self.rules) if matches(r["if"], user, attrs or {})), None)

    def evaluate(self, user, attrs=None):
        """(variant, reason)"""
        if not self.enabled:
            return self.control, "flag off"
        i = self.rule_for(user, attrs)
        if i is not None:
            return self.rules[i]["serve"], f"rule {i}"
        pct = self.canary.percent()
        on = bucket(self.name, user) < round(pct * BUCKETS / 100)
        return (self.candidate if on else self.control), f"rollout {pct}%"


class Platform:
    """The flags, their rollouts, and optionally a file their state survives restarts in."""

    def __init__(self, flags, state_path=None):
        self.flags = {f.name: f for f in flags}
        self.state_path = Path(state_path) if state_path else None
        if self.state_path and self.state_path.exists():
            for name, s in json.loads(self.state_path.read_text()).items():
                if name in self.flags:
                    self.flags[name].enabled = s["enabled"]
                    self.flags[name].canary = Canary(**s["canary"])

    @classmethod
    def load(cls, config, state_path=None):
        flags = yaml.safe_load(Path(config).read_text())["flags"]
        return cls([Flag.from_config(n, c) for n, c in flags.items()], state_path)

    def save(self):
        if self.state_path:
            state = {n: {"enabled": f.enabled, "canary": f.canary.to_dict()} for n, f in self.flags.items()}
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            tmp.replace(self.state_path)

    def evaluate(self, name, user, attrs=None):
        f = self.flags[name]
        variant, reason = f.evaluate(user, attrs)
        return {"flag": name, "variant": variant, "config": f.variants[variant], "reason": reason}

    def score(self, name, user, variant, score, segment=None, attrs=None):
        """How a served request went, from 0 to 1. Only requests the rollout assigned count:
        not those a targeting rule decided, and none while the flag is off."""
        f, score = self.flags[name], float(score)
        if not 0 <= score <= 1:
            raise ValueError("score must be between 0 and 1")
        if variant not in (f.control, f.candidate):
            raise ValueError(f"unknown variant {variant!r}")
        c = f.canary
        if f.enabled and c.state == "running" and f.rule_for(user, attrs) is None:
            stage = c.stage
            c.record(int(variant == f.candidate), score, segment)
            if c.state != "running" or c.stage != stage or c.seen % c.check_every == 0:
                self.save()
        return self.status(name)

    def set_enabled(self, name, on):
        self.flags[name].enabled = bool(on)
        self.save()
        return self.status(name)

    def status(self, name):
        f = self.flags[name]
        c = f.canary
        def arm(a):
            n = int(c.counts[0, a, 0])
            return {"requests": n, "mean": round(float(c.counts[0, a, 1]) / n, 4) if n else None}
        return {"flag": name, "enabled": f.enabled, "state": c.state, "percent": c.percent() if f.enabled else 0,
                "scored": c.seen, "control": arm(0), "candidate": arm(1), "reason": c.reason, "log": c.log[-20:]}
