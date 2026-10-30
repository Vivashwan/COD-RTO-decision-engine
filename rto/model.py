"""RTO probability model: smoothed area history + a small logistic regression.

Why logistic regression and not something fancier: 2,000 rows, ~13 features,
and every decision needs a plain-English reason. Each coefficient maps to one
sentence an ops person can read, and the probabilities come out calibrated
enough to do ₹ maths on. Pure Python so the repo runs with no dependencies.
"""
import json
import math
import random
from collections import defaultdict

SMOOTHING = 20  # an area needs ~20 orders before its own history outweighs the wider region


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def sigmoid(z):
    return 1 / (1 + math.exp(-z)) if z > -35 else 0.0


def is_rto(o):
    return 1 if o["final_status"] == "RTO" else 0


# ---------------------------------------------------------------- area risk

class AreaRisk:
    """RTO rate by pincode district (first 3 digits), shrunk toward the state
    rate, which is shrunk toward the overall rate. Full pincodes are too sparse
    to use (most have 1-2 orders), see reports/findings.md."""

    def __init__(self, k=SMOOTHING):
        self.k = k
        self.global_rate = 0.0
        self.state = {}
        self.pin3 = {}
        self.city_pin3 = {}
        self.names = {}

    def fit(self, orders):
        n = len(orders)
        self.global_rate = sum(map(is_rto, orders)) / n
        by_state, by_pin3 = defaultdict(list), defaultdict(list)
        city_votes = defaultdict(lambda: defaultdict(int))
        for o in orders:
            by_state[o["state"]].append(is_rto(o))
            if o["pincode"]:
                p3 = o["pincode"][:3]
                by_pin3[p3].append((o["state"], is_rto(o)))
                city_votes[o["city"].lower()][p3] += 1
                self.names.setdefault(p3, o["city"])
        for s, ys in by_state.items():
            self.state[s] = {"n": len(ys), "rate": (sum(ys) + self.k * self.global_rate) / (len(ys) + self.k)}
        for p3, rows in by_pin3.items():
            st = max(set(r[0] for r in rows), key=lambda s: sum(r[0] == s for r in rows))
            prior = self.state.get(st, {"rate": self.global_rate})["rate"]
            ys = [r[1] for r in rows]
            self.pin3[p3] = {"n": len(ys), "raw": sum(ys) / len(ys),
                             "rate": (sum(ys) + self.k * prior) / (len(ys) + self.k), "state": st}
        self.city_pin3 = {c: max(v, key=v.get) for c, v in city_votes.items()}
        return self

    def lookup(self, order):
        """Returns (rate, n_history, label) for an order's delivery area."""
        p3 = order["pincode"][:3] if order["pincode"] else self.city_pin3.get(order["city"].lower())
        if p3 and p3 in self.pin3:
            a = self.pin3[p3]
            return a["rate"], a["n"], f"{self.names.get(p3, order['city'])} ({p3}xxx)"
        if order["state"] in self.state:
            s = self.state[order["state"]]
            return s["rate"], s["n"], order["state"]
        return self.global_rate, 0, "new area"

    def to_dict(self):
        return {"k": self.k, "global_rate": self.global_rate, "state": self.state,
                "pin3": self.pin3, "city_pin3": self.city_pin3, "names": self.names}

    @classmethod
    def from_dict(cls, d):
        a = cls(d["k"])
        a.global_rate, a.state, a.pin3 = d["global_rate"], d["state"], d["pin3"]
        a.city_pin3, a.names = d["city_pin3"], d["names"]
        return a


# ---------------------------------------------------------------- features

FEATURES = [
    "area_history",        # logit of the area's smoothed RTO rate, centred on the overall rate
    "address_landmark_only",
    "address_unusable",
    "name_implausible",
    "phone_invalid",
    "pincode_missing",
    "repeat_no_returns",
    "prior_rto_1",
    "prior_rto_2plus",
    "order_value_1500plus",
    "single_item",
    "source_meta_ads",
    "source_direct",
]


def featurise(order, judgement, area_rate, global_rate):
    q = judgement["address_quality"]
    prior_o, prior_r = order["customer_prior_orders"], order["customer_prior_rto"]
    return {
        "area_history": logit(area_rate) - logit(global_rate),
        "address_landmark_only": float(q == "landmark_only"),
        "address_unusable": float(q == "unusable"),
        "name_implausible": float(not judgement["name_plausible"]),
        "phone_invalid": float(order["phone"] is None),
        "pincode_missing": float(not order["pincode"]),
        "repeat_no_returns": float(prior_o > 0 and prior_r == 0),
        "prior_rto_1": float(prior_r == 1),
        "prior_rto_2plus": float(prior_r >= 2),
        "order_value_1500plus": float(order["order_value"] >= 1500),
        "single_item": float(order["num_items"] == 1),
        "source_meta_ads": float(order["utm_source"] == "meta_ads"),
        "source_direct": float(order["utm_source"] == "direct"),
    }


# ---------------------------------------------------------------- logistic regression

def fit_logreg(X, y, l2=1.0, lr=0.5, epochs=3000):
    """Batch gradient descent with L2. X: list of feature dicts."""
    n = len(X)
    w = {f: 0.0 for f in FEATURES}
    b = logit(sum(y) / n)
    for _ in range(epochs):
        gw = {f: 0.0 for f in FEATURES}
        gb = 0.0
        for x, t in zip(X, y):
            err = sigmoid(b + sum(w[f] * x[f] for f in FEATURES)) - t
            gb += err
            for f in FEATURES:
                if x[f]:
                    gw[f] += err * x[f]
        b -= lr * gb / n
        for f in FEATURES:
            w[f] -= lr * (gw[f] / n + l2 * w[f] / n)
    return w, b


class RiskModel:
    def __init__(self):
        self.area = None
        self.weights = {}
        self.bias = 0.0
        self.meta = {}

    def fit(self, orders, judgements, folds=5, seed=7):
        """judgements: list aligned with orders. Area rates for training rows are
        computed out-of-fold so a row's own outcome never leaks into its feature."""
        self.area = AreaRisk().fit(orders)
        idx = list(range(len(orders)))
        random.Random(seed).shuffle(idx)
        oof_rate = [None] * len(orders)
        for k in range(folds):
            hold = set(idx[k::folds])
            fold_area = AreaRisk().fit([o for i, o in enumerate(orders) if i not in hold])
            for i in hold:
                oof_rate[i] = fold_area.lookup(orders[i])[0]
        g = self.area.global_rate
        X = [featurise(o, j, r, g) for o, j, r in zip(orders, judgements, oof_rate)]
        y = [is_rto(o) for o in orders]
        self.weights, self.bias = fit_logreg(X, y)
        self.meta = {"n_train": len(orders), "train_rto_rate": g,
                     "from": min(o["order_datetime"] for o in orders).strftime("%Y-%m-%d"),
                     "to": max(o["order_datetime"] for o in orders).strftime("%Y-%m-%d")}
        return self

    def score(self, order, judgement):
        rate, n_hist, label = self.area.lookup(order)
        x = featurise(order, judgement, rate, self.area.global_rate)
        contrib = {f: self.weights[f] * x[f] for f in FEATURES if x[f]}
        p = sigmoid(self.bias + sum(contrib.values()))
        return {"p": p, "contributions": contrib, "features": x,
                "area": {"rate": rate, "n": n_hist, "label": label,
                         "raw": self.area.pin3.get((order["pincode"] or "")[:3], {}).get("raw")}}

    def save(self, path):
        with open(path, "w") as f:
            json.dump({"weights": self.weights, "bias": self.bias, "meta": self.meta,
                       "area": self.area.to_dict()}, f, indent=1)

    @classmethod
    def load(cls, path):
        with open(path) as f:
            d = json.load(f)
        m = cls()
        m.weights, m.bias, m.meta = d["weights"], d["bias"], d["meta"]
        m.area = AreaRisk.from_dict(d["area"])
        return m


# ---------------------------------------------------------------- evaluation helpers

def auc(scores, labels):
    pairs = sorted(zip(scores, labels))
    pos = sum(labels)
    neg = len(labels) - pos
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j + 1) / 2
        rank_sum += avg_rank * sum(l for _, l in pairs[i:j])
        i = j
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg) if pos and neg else float("nan")


def calibration(scores, labels, bins=5):
    rows = sorted(zip(scores, labels))
    out = []
    size = math.ceil(len(rows) / bins)
    for k in range(0, len(rows), size):
        chunk = rows[k:k + size]
        out.append({"predicted": sum(s for s, _ in chunk) / len(chunk),
                    "actual": sum(l for _, l in chunk) / len(chunk), "n": len(chunk)})
    return out
