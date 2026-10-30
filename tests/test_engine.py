"""python3 -m unittest discover -s tests      (runs offline; the LLM is disabled)"""
import os
import sys
import unittest

os.environ["RTO_DISABLE_LLM"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rto.data import normalise_phone, parse_order  # noqa: E402
from rto.economics import Economics  # noqa: E402
from rto.engine import MODEL_PATH, Engine, load_policy  # noqa: E402
from rto.judge import heuristic_judge, heuristic_reply  # noqa: E402
from rto.whatsapp import Conversation  # noqa: E402

BASE = dict(order_id="T1", order_datetime="2026-09-30 11:00", customer_name="Pooja Yadav",
            phone="9876543210", address="House No. 12, Sector 4, near SBI Bank", city="Pune",
            state="Maharashtra", pincode="411001", order_value="649", items="Satin Bonnet",
            num_items="1", customer_prior_orders="0", customer_prior_rto="0", utm_source="direct",
            courier="Delhivery")


def order(**kw):
    return parse_order({**BASE, **kw})


class Economy(unittest.TestCase):
    def setUp(self):
        self.e = Economics(load_policy())

    def test_rto_cost_matches_brief(self):
        self.assertAlmostEqual(self.e.rto_loss, 65 + 65 + 25 + 0.08 * 180)

    def test_shipping_is_the_zero_baseline(self):
        self.assertEqual(self.e.impact("ship", 0.4, 649, True)["net"], 0)

    def test_cancelling_an_average_order_loses_money(self):
        self.assertLess(self.e.impact("hold", 0.28, 649, True)["net"], 0)

    def test_cancel_breakeven_is_where_hold_turns_positive(self):
        p = self.e.cancel_breakeven(649, False)
        # hold also offers a prepaid link, so it turns positive a little before pure cancel would
        self.assertGreater(self.e.impact("hold", p + 0.02, 649, False)["net"], 0)
        self.assertLess(self.e.impact("hold", p - 0.10, 649, False)["net"], 0)

    def test_expected_equals_mix_of_outcomes(self):
        for a in ("nudge", "confirm", "hold"):
            p = 0.37
            mix = p * self.e.impact_actual(a, True, p, 899, True)["net"] + \
                (1 - p) * self.e.impact_actual(a, False, p, 899, True)["net"]
            self.assertAlmostEqual(self.e.impact(a, p, 899, True)["net"], mix)


class Judge(unittest.TestCase):
    def test_hinglish_landmark_is_not_junk(self):
        self.assertEqual(heuristic_judge("Riya", "ph krke aana, Govt School ke paas", "Patna")["address_quality"], "landmark_only")

    def test_junk(self):
        for a in (".", "xyz", "call me", "Patna", "near station"):
            self.assertEqual(heuristic_judge("Riya", a, "Patna")["address_quality"], "unusable", a)

    def test_complete(self):
        self.assertEqual(heuristic_judge("Riya", "House No. 380, Sector 24, near Water Tank", "Jaipur")["address_quality"], "complete")

    def test_fake_names(self):
        for n in ("asdf", "qwerty", "Test Order", "xyz xyz", "na", "aaa"):
            self.assertFalse(heuristic_judge(n, "Flat 1, MG Road", "Pune")["name_plausible"], n)
        self.assertTrue(heuristic_judge("Na Yeon Kim", "Flat 1, MG Road", "Pune")["name_plausible"])

    def test_replies(self):
        cases = {"haan bhej do": "yes", "YES": "yes", "👍": "yes", "nahi chahiye": "no", "mat bhejo": "no",
                 "PAY": "pay_online", "H.No 12, Shastri Nagar, Patna": "address_update",
                 "kitne din mein aayega?": "unclear", "Hindi": "language_switch"}
        for text, intent in cases.items():
            self.assertEqual(heuristic_reply(text)["intent"], intent, text)

    def test_phone_formats(self):
        self.assertEqual(normalise_phone("+91 98765 43210")[0], "9876543210")
        self.assertIsNone(normalise_phone("987654321")[0])


@unittest.skipUnless(os.path.exists(MODEL_PATH), "run scripts/train.py first")
class Decisions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = Engine()

    def test_good_order_ships(self):
        d = self.eng.decide(order(customer_prior_orders="3"))
        self.assertEqual(d["action"], "ship")
        self.assertIn("chance of return", d["reason"])

    def test_fake_name_and_junk_address_held(self):
        d = self.eng.decide(order(customer_name="asdf", address="xyz", city="Darbhanga", state="Bihar", pincode="846004"))
        self.assertEqual(d["action"], "hold")

    def test_repeat_refuser_held(self):
        d = self.eng.decide(order(customer_prior_orders="3", customer_prior_rto="2"))
        self.assertEqual(d["action"], "hold")

    def test_first_time_buyer_not_auto_cancelled_by_score(self):
        d = self.eng.decide(order(address="near station", city="Gorakhpur", state="Uttar Pradesh",
                                  pincode="273001", order_value="1797", utm_source="meta_ads"))
        self.assertGreaterEqual(d["risk_pct"], 75)
        self.assertEqual(d["action"], "confirm")

    def test_invalid_phone_ships_instead_of_whatsapp(self):
        d = self.eng.decide(order(phone="98765432", city="Patna", state="Bihar", pincode="800001", order_value="1597"))
        self.assertNotIn(d["action"], ("nudge", "confirm"))

    def test_double_submit_goes_to_confirm(self):
        first = order(order_id="A1", customer_prior_orders="2")
        second = order(order_id="A2", customer_prior_orders="2", order_datetime="2026-09-30 11:03")
        d = self.eng.decide(second, recent_orders=[first])
        self.assertEqual(d["action"], "confirm")
        self.assertIn("A1", d["reason"])

    def test_thresholds_come_from_config(self):
        pol = load_policy()
        pol["thresholds"].update(nudge_from=0, confirm_from=101, hold_from=101)
        eng = Engine(policy=pol, model=self.eng.model)
        self.assertEqual(eng.decide(order(customer_prior_orders="3"))["action"], "nudge")


@unittest.skipUnless(os.path.exists(MODEL_PATH), "run scripts/train.py first")
class WhatsApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = Engine()

    def convo(self, **kw):
        o = order(address="near station", city="Gorakhpur", state="Uttar Pradesh", pincode="273001", **kw)
        d = self.eng.decide(o)
        self.assertEqual(d["action"], "confirm")
        return Conversation(o, d, self.eng.policy, use_llm=False)

    def test_hinglish_for_up(self):
        c = self.convo()
        self.assertEqual(c.lang, "hi")
        self.assertIn("Namaste", c.messages[0]["text"])

    def test_yes_then_address_confirms(self):
        c = self.convo()
        c.receive("haan bhej do")
        self.assertEqual(c.state, "awaiting_address")
        c.receive("H.No 45, Rapti Nagar Phase 2, near Bus Stand")
        self.assertEqual(c.warehouse_action, "ship_cod")

    def test_no_cancels(self):
        c = self.convo()
        c.receive("nahi chahiye")
        self.assertEqual(c.warehouse_action, "cancel")

    def test_silence_gets_reminder_last_call_then_cancel(self):
        c = self.convo()
        c.advance(30)
        texts = " ".join(m["text"] for m in c.messages if m["dir"] == "out")
        self.assertIn("Bas confirm", texts)
        self.assertIn("Aakhri reminder", texts)
        self.assertEqual(c.state, "cancelled_no_reply")

    def test_night_order_waits_for_morning_and_last_call_not_skipped(self):
        c = self.convo(order_datetime="2026-09-30 02:10")
        self.assertEqual(c.state, "scheduled")
        c.advance(60)
        outs = [m for m in c.messages if m["dir"] == "out"]
        self.assertTrue(outs[0]["at"].endswith("08:00:00"))
        for m in outs:
            self.assertFalse(21 <= int(m["at"][11:13]) or int(m["at"][11:13]) < 8, m)
        self.assertTrue(any("Aakhri" in m["text"] for m in outs))

    def test_unclear_twice_goes_to_a_person(self):
        c = self.convo()
        c.receive("asdfgh")
        c.receive("??")
        self.assertTrue(c.agent_flag)

    def test_payment(self):
        c = self.convo()
        c.receive("PAY")
        c.payment_received()
        self.assertEqual(c.warehouse_action, "ship_prepaid")


if __name__ == "__main__":
    unittest.main()
