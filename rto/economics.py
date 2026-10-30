"""₹ maths for each action. All plain arithmetic, all inputs from config/policy.json.

Every action is compared with "ship as COD" for the same order, split by how the
order would have ended if we had just shipped it:

  saved  - on orders that WOULD have come back: RTO cost avoided, plus margin recovered
           when that customer pays online instead and keeps the order
  lost   - on orders that WOULD have been delivered: margin (and a first-time buyer's
           future orders) given up because we cancelled or they dropped off
  cost   - prepaid discounts + WhatsApp fees
  net    = saved - lost - cost

impact_actual() does this for a past order whose outcome we know (back-testing);
impact() is the same thing weighted by the model's RTO probability (live orders).
"""

ACTIONS = ("ship", "nudge", "confirm", "hold")


class Economics:
    def __init__(self, policy):
        e, a = policy["economics"], policy["assumptions"]
        self.gm = e["gross_margin_pct"] / 100
        self.fwd = e["forward_shipping"]
        self.rto_loss = (e["forward_shipping"] + e["return_shipping"] + e["packaging_handling"]
                         + e["rto_damage_rate_pct"] / 100 * e["damaged_product_cost"])
        self.pp = e["prepaid_rto_rate_pct"] / 100
        self.future = a["future_value_first_time_buyer"]
        self.wa = a["whatsapp_cost_per_conversation"]
        self.catch = a["confirm_catches_rto_pct"] / 100
        self.lose = a["confirm_loses_genuine_pct"] / 100
        self.conv = a["nudge_conversion_pct"] / 100
        self.conv_risk = a["nudge_converter_risk_factor"]
        self.hold_conv = a["hold_prepaid_conversion_pct"] / 100
        self.discount = policy["prepaid_nudge"]["discount_rupees"]

    def delivered_value(self, order_value, first_time):
        """Contribution of one delivered order: product margin minus forward shipping,
        plus the expected value of future orders for a first-time buyer."""
        return self.gm * order_value - self.fwd + (self.future if first_time else 0)

    def _pays_online(self, action, rto, p):
        """Chance this customer switches to prepaid, given how the order would have ended.
        People who accept are less risky than their band (conv_risk < 1), so a would-be
        RTO is less likely to accept than a genuine buyer."""
        share = {"nudge": self.conv, "hold": self.hold_conv}.get(action, 0.0)
        if rto:
            return share * self.conv_risk
        return min(1.0, share * (1 - self.conv_risk * p) / (1 - p)) if p < 1 else share

    def impact_actual(self, action, rto, p, order_value, first_time):
        V = self.delivered_value(order_value, first_time)
        L = self.rto_loss
        saved = lost = 0.0
        cost = 0.0 if action == "ship" else self.wa
        if action == "confirm":
            if rto:
                saved = self.catch * L
            else:
                lost = self.lose * V
        elif action in ("nudge", "hold"):
            q = self._pays_online(action, rto, p)
            cost += q * self.discount
            # prepaid orders still come back at the prepaid rate
            prepaid_value = (1 - self.pp) * V - self.pp * L
            if rto:
                # COD would have failed: converters now mostly deliver; hold also stops
                # the rest from shipping, nudge ships them anyway.
                stopped = (1 - q) if action == "hold" else 0.0
                saved = q * (L + prepaid_value) + stopped * L
            else:
                # COD would have delivered: converters deliver prepaid (tiny RTO risk);
                # hold loses everyone who doesn't convert.
                kept = q * prepaid_value + ((1 - q) * V if action == "nudge" else 0.0)
                lost = V - kept
        return {"saved": saved, "lost": lost, "cost": cost, "net": saved - lost - cost}

    def impact(self, action, p, order_value, first_time):
        a = self.impact_actual(action, True, p, order_value, first_time)
        b = self.impact_actual(action, False, p, order_value, first_time)
        return {k: p * a[k] + (1 - p) * b[k] for k in a}

    def best_action(self, p, order_value, first_time):
        return max(ACTIONS, key=lambda a: self.impact(a, p, order_value, first_time)["net"])

    def cancel_breakeven(self, order_value, first_time):
        """Risk above which cancelling outright beats shipping: (1-p)V = pL."""
        V = self.delivered_value(order_value, first_time)
        return V / (V + self.rto_loss)

    def confirm_breakeven(self, order_value, first_time):
        """Risk above which WhatsApp confirmation beats shipping: p*catch*L = (1-p)*lose*V + fee."""
        V = self.delivered_value(order_value, first_time)
        a, b = self.catch * self.rto_loss, self.lose * V
        return (b + self.wa) / (a + b)

    def max_sensible_discount(self, p, order_value, first_time):
        """Largest discount that still pays for itself on a converting order."""
        V = self.delivered_value(order_value, first_time)
        p_conv = self.conv_risk * p
        pp = min(self.pp, p_conv)
        return (p_conv - pp) * (V + self.rto_loss)
