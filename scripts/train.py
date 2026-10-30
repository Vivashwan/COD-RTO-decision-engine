"""Train the RTO risk model.

    python3 scripts/train.py            # train on Apr-Aug, keep September as an honest test month
    python3 scripts/train.py --all      # train on every month (what you'd deploy)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rto.data import load_orders, split_by_month  # noqa: E402
from rto.engine import MODEL_PATH, ROOT, load_policy  # noqa: E402
from rto.judge import flush_cache, judge_order, llm_available  # noqa: E402
from rto.model import RiskModel, auc, calibration, is_rto  # noqa: E402

HOLDOUT = "2026-09"


def judge_all(orders, policy):
    llm = policy["llm"]
    return [judge_order(o["customer_name"], o["address"], o["city"], o["pincode"],
                        model=llm["model"], use_llm=llm["enabled"]) for o in orders]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="train on all months (no holdout)")
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "orders_sample.csv"))
    args = ap.parse_args()

    policy = load_policy()
    orders, quality = load_orders(args.data)
    train, test = (orders, []) if args.all else split_by_month(orders, HOLDOUT)
    print(f"judging addresses/names with {'Claude' if llm_available() and policy['llm']['enabled'] else 'offline heuristics'}...")
    model = RiskModel().fit(train, judge_all(train, policy))
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    model.save(MODEL_PATH)
    flush_cache()
    print(f"trained on {len(train)} orders ({model.meta['from']} to {model.meta['to']}) -> {MODEL_PATH}")
    for f, w in sorted(model.weights.items(), key=lambda kv: -abs(kv[1])):
        print(f"  {f:24} {w:+.2f}")
    if test:
        s = [model.score(o, j)["p"] for o, j in zip(test, judge_all(test, policy))]
        y = [is_rto(o) for o in test]
        print(f"holdout {HOLDOUT}: {len(test)} orders, AUC {auc(s, y):.3f}")
        for c in calibration(s, y):
            print(f"  predicted {c['predicted']:.0%}  actual {c['actual']:.0%}  (n={c['n']})")


if __name__ == "__main__":
    main()
