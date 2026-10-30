"""Replay past orders through a policy and add up the ₹."""
from collections import defaultdict

from .economics import ACTIONS, Economics


def prepare(orders, engine):
    """Judge + score each order once; policies can then be replayed cheaply."""
    out = []
    for o in orders:
        j = engine.judge(o)
        out.append((o, j, engine.model.score(o, j)))
    return out


def run(prepared, engine, policy=None, use_recent=True):
    """Returns (decisions, summary). Summary has per-bucket counts plus ₹ totals two ways:
    'expected' (model probabilities, what the live dashboard shows) and 'actual'
    (using what really happened to each order, only possible on history)."""
    if policy is not None:
        engine.reload_policy(policy)
    econ: Economics = engine.econ
    decisions = []
    recent = []
    for o, j, s in sorted(prepared, key=lambda t: t[0]["order_datetime"]):
        if use_recent:
            recent = [r for r in recent if (o["order_datetime"] - r["order_datetime"]).total_seconds() <= 86400]
        d = engine.decide(o, judgement=j, score=s, recent_orders=recent if use_recent else None)
        if o.get("final_status"):
            d["actual"] = o["final_status"]
            d["impact_actual"] = econ.impact_actual(d["action"], o["final_status"] == "RTO", s["p"],
                                                    o["order_value"], d["first_time"])
        d["order"] = o
        decisions.append(d)
        recent.append(o)
    return decisions, summarise(decisions)


def summarise(decisions):
    s = {"orders": len(decisions),
         "buckets": {a: 0 for a in ACTIONS},
         "rto_in_bucket": {a: 0 for a in ACTIONS},
         "expected": defaultdict(float), "actual": defaultdict(float)}
    for d in decisions:
        a = d["action"]
        s["buckets"][a] += 1
        if d.get("actual") == "RTO":
            s["rto_in_bucket"][a] += 1
        for k in ("saved", "lost", "cost", "net"):
            s["expected"][k] += d["impact"][k]
            if "impact_actual" in d:
                s["actual"][k] += d["impact_actual"][k]
    s["expected"] = {k: round(v) for k, v in s["expected"].items()}
    s["actual"] = {k: round(v) for k, v in s["actual"].items()}
    s["actual_rto_orders"] = sum(d.get("actual") == "RTO" for d in decisions)
    return s
