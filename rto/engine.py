"""Decide what to do with a COD order: ship / nudge to prepaid / confirm on WhatsApp / hold.

    order -> LLM/heuristic judgement of address+name -> risk model -> thresholds
          -> hard rules -> action + plain-English reason + ₹ impact
"""
import json
import os
from datetime import timedelta

from .data import parse_order
from .economics import Economics
from .judge import judge_order
from .model import RiskModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_PATH = os.path.join(ROOT, "config", "policy.json")
MODEL_PATH = os.path.join(ROOT, "models", "risk_model.json")

LABELS = {"ship": "Ship as-is", "nudge": "Nudge to prepaid",
          "confirm": "Ship after WhatsApp confirmation", "hold": "Hold / cancel COD"}
RANK = {"ship": 0, "nudge": 1, "confirm": 2, "hold": 3}
NEXT_STEP = {
    "ship": "Ship today.",
    "nudge": "WhatsApp offers ₹{discount} off to pay online; ships as COD if they don't.",
    "confirm": "WhatsApp asks the customer to confirm before we ship.",
    "hold": "Don't ship COD. WhatsApp offers a pay-online link instead.",
}


def load_policy(path=POLICY_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_policy(policy, path=POLICY_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(policy, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _pct(x):
    return f"{round(100 * x)}%"


def explain(order, judgement, score):
    """Turn model contributions into short sentences, biggest effect first.
    Returns (risk_raising, risk_lowering) lists of strings."""
    area = score["area"]
    texts = {
        "area_history": (
            f"{area['label']}: {_pct(area['raw'] if area['raw'] is not None else area['rate'])} of past COD orders "
            f"came back ({area['n']} orders)" if score["contributions"].get("area_history", 0) > 0
            else f"{area['label']} is a low-return area ({_pct(area['rate'])} come back)"),
        "address_landmark_only": "address has only a landmark, no house/flat number",
        "address_unusable": f"address \"{order['address']}\" is too vague for a courier to find",
        "name_implausible": f"name \"{order['customer_name']}\" looks fake",
        "phone_invalid": f"phone \"{order['phone_raw']}\" is not a valid mobile number",
        "pincode_missing": "pincode is missing",
        "repeat_no_returns": f"repeat customer: {order['customer_prior_orders']} earlier orders, none returned",
        "prior_rto_1": "refused 1 earlier COD order",
        "prior_rto_2plus": f"refused {order['customer_prior_rto']} earlier COD orders",
        "order_value_1500plus": f"high-value COD order (₹{order['order_value']:.0f})",
        "single_item": "single-item order",
        "source_meta_ads": "came from a Meta ad (returns more than other channels)",
        "source_direct": "came to the website directly",
    }
    contrib = sorted(score["contributions"].items(), key=lambda kv: -abs(kv[1]))
    up = [texts[f] for f, c in contrib if c > 0.15]
    down = [texts[f] for f, c in contrib if c < -0.15]
    return up, down


class Engine:
    def __init__(self, policy=None, model=None):
        self.policy = policy or load_policy()
        self.model = model or RiskModel.load(MODEL_PATH)
        self.econ = Economics(self.policy)

    def reload_policy(self, policy=None):
        self.policy = policy or load_policy()
        self.econ = Economics(self.policy)

    def judge(self, order):
        llm = self.policy["llm"]
        return judge_order(order["customer_name"], order["address"], order["city"], order["pincode"],
                           model=llm["model"], use_llm=llm["enabled"])

    def decide(self, order, judgement=None, recent_orders=None, score=None):
        """order: parsed order dict (see data.parse_order). recent_orders: other
        orders from the last few hours, used to catch accidental double-submits. judgement and
        score can be passed in when replaying many policies over the same orders."""
        judgement = judgement or self.judge(order)
        score = score or self.model.score(order, judgement)
        p = score["p"]
        t, r = self.policy["thresholds"], self.policy["rules"]
        first_time = order["customer_prior_orders"] == 0

        if p * 100 >= t["hold_from"]:
            action = "hold"
        elif p * 100 >= t["confirm_from"]:
            action = "confirm"
        elif p * 100 >= t["nudge_from"]:
            action = "nudge"
        else:
            action = "ship"
        rule = None

        # Rules that can only make the action stricter.
        def at_least(a, why):
            nonlocal action, rule
            if RANK[a] > RANK[action]:
                action, rule = a, why

        if r.get("confirm_if_address_unusable") and judgement["address_quality"] == "unusable":
            at_least("confirm", "address too vague: confirm and ask for full address")
        if r.get("confirm_if_name_implausible") and not judgement["name_plausible"]:
            at_least("confirm", "name looks fake: confirm with the customer")
        if r.get("confirm_if_pincode_missing") and not order["pincode"]:
            at_least("confirm", "pincode missing: confirm and ask for it")

        # Marketing protection: the score alone never cancels a first-time buyer.
        cap = r.get("first_time_buyers_max_action")
        if first_time and cap in RANK and RANK[action] > RANK[cap] and rule is None:
            action, rule = cap, f"first-time buyer: we {LABELS[cap].lower()} instead of cancelling"

        # Hard stops: these apply to everyone.
        if r.get("hold_if_zero_value") and order["order_value"] <= 0:
            action, rule = "hold", "₹0 order: looks like a test/internal order, check manually"
        if order["phone"] is None:
            if r.get("hold_if_phone_invalid"):
                action, rule = "hold", "phone number invalid: neither WhatsApp nor the courier can reach them"
            elif action in ("nudge", "confirm"):
                # Can't WhatsApp a broken number. Half of these still get delivered,
                # which beats cancelling (₹), so ship and let the courier sort it out.
                action, rule = "ship", "phone number invalid: can't WhatsApp, ship and flag to courier"
        k = r.get("hold_if_prior_rto_at_least")
        if k and order["customer_prior_rto"] >= k:
            action, rule = "hold", f"customer refused {order['customer_prior_rto']} earlier COD orders"
        if (r.get("hold_if_fake_name_and_unusable_address") and not judgement["name_plausible"]
                and judgement["address_quality"] == "unusable"):
            action, rule = "hold", "fake-looking name AND unusable address"
        dup = self._find_duplicate(order, recent_orders or [])
        if dup:
            # Ask, don't cancel: in the history both copies were often delivered.
            at_least("confirm", f"possible double order: same phone, address and items as {dup['order_id']} within 2h")

        up, down = explain(order, judgement, score)
        impact = self.econ.impact(action, p, order["order_value"], first_time)
        best = self.econ.best_action(p, order["order_value"], first_time)

        why = up[:3] if up else ["nothing unusual"]
        reason = f"{_pct(p)} chance of return: " + "; ".join(why)
        if down:
            reason += f". In its favour: {down[0]}"
        reason += "."
        if rule:
            reason = f"Rule: {rule}. " + reason
        reason += " " + NEXT_STEP[action].format(discount=self.policy["prepaid_nudge"]["discount_rupees"])

        return {
            "order_id": order["order_id"],
            "action": action,
            "action_label": LABELS[action],
            "risk_pct": round(100 * p, 1),
            "reason": reason,
            "rule": rule,
            "risk_up": up,
            "risk_down": down,
            "judgement": judgement,
            "first_time": first_time,
            "impact": {k: round(v, 2) for k, v in impact.items()},
            "money_best_action": best,
            "area": score["area"],
        }

    def decide_raw(self, row, recent_orders=None):
        return self.decide(parse_order(row), recent_orders=recent_orders)

    @staticmethod
    def _find_duplicate(order, recent):
        for o in recent:
            if (o["order_id"] != order["order_id"] and o["phone"] and o["phone"] == order["phone"]
                    and sorted(o["items"]) == sorted(order["items"])
                    and o["address"].lower() == order["address"].lower()
                    and abs(o["order_datetime"] - order["order_datetime"]) <= timedelta(hours=2)):
                return o
        return None
