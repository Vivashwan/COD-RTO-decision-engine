"""WhatsApp flows for "confirm", "nudge to prepaid" and "hold", with a mock sender.

Nothing here talks to Meta. MockWhatsApp records each outgoing message in the shape
the WhatsApp Cloud API expects (interactive reply buttons), so swapping in a real
BSP later means replacing one send() method.

Time is simulated: each conversation has its own clock that the simulator moves
forward, so "no reply in 24 hours" can be demonstrated in a second.
"""
import itertools
from datetime import timedelta

from .judge import judge_order, parse_reply

PAY_LINK = "https://pay.example/{order_id}"

T = {
    "confirm": {
        "en": ("Hi {first}! 👋 Thanks for your {brand} order:\n{items} – ₹{value:.0f} (Cash on Delivery)\n\n"
               "Please confirm so we can ship it:\n✅ YES – ship it\n💳 PAY – pay online & get ₹{d} off\n❌ NO – cancel"),
        "hi": ("Namaste {first}! 👋 {brand} se aapka order:\n{items} – ₹{value:.0f} (Cash on Delivery)\n\n"
               "Ship karne se pehle confirm kar dijiye:\n✅ YES – bhej do\n💳 PAY – online pay karo, ₹{d} ki chhoot\n❌ NO – cancel"),
    },
    "address_ask": {
        "en": "📍 Your address looks incomplete (\"{address}\"). Please reply with house/flat no., street and area so the courier can find you.",
        "hi": "📍 Aapka address adhoora lag raha hai (\"{address}\"). Kripya ghar/flat no., gali aur area likh kar bhejiye taaki courier aap tak pahunch sake.",
    },
    "nudge": {
        "en": ("Hi {first}! Your {brand} order ({items}, ₹{value:.0f}) is confirmed and ships as Cash on Delivery. 🎉\n\n"
               "💳 Pay online now and get ₹{d} off – just ₹{paid:.0f}: {link}\n"
               "Prefer cash? No need to do anything."),
        "hi": ("Namaste {first}! Aapka {brand} order ({items}, ₹{value:.0f}) confirm ho gaya hai, Cash on Delivery se aayega. 🎉\n\n"
               "💳 Abhi online pay karein aur ₹{d} bachayein – sirf ₹{paid:.0f}: {link}\n"
               "Cash mein dena hai? Kuch karne ki zaroorat nahi."),
    },
    "hold": {
        "en": ("Hi {first}, about your {brand} order ({items}, ₹{value:.0f}): we can't send this one as Cash on Delivery. 🙏\n\n"
               "You can still get it – pay online and we ship today, with ₹{d} off (₹{paid:.0f}): {link}\n"
               "Questions? Just reply here."),
        "hi": ("Namaste {first}, aapke {brand} order ({items}, ₹{value:.0f}) ke baare mein: yeh order Cash on Delivery se nahi bhej paayenge. 🙏\n\n"
               "Online pay karein to aaj hi ship karenge, ₹{d} ki chhoot ke saath (₹{paid:.0f}): {link}\n"
               "Koi sawaal ho to yahin reply karein."),
    },
    "reminder": {
        "en": "Just checking – shall we ship your {brand} order? Reply YES to ship or NO to cancel.",
        "hi": "Bas confirm kar rahe hain – aapka {brand} order bhej dein? YES ya NO likhiye.",
    },
    "last_call": {
        "en": "⏰ Last reminder: we'll cancel your Cash on Delivery order in {left} hours if we don't hear from you. Reply YES to ship it.",
        "hi": "⏰ Aakhri reminder: {left} ghante mein jawab nahi mila to COD order cancel ho jayega. Bhejne ke liye YES likhiye.",
    },
    "confirmed": {
        "en": "✅ Confirmed! Your order ships today. The courier will call before delivery.",
        "hi": "✅ Confirm ho gaya! Aapka order aaj ship hoga. Delivery se pehle courier call karega.",
    },
    "address_thanks": {
        "en": "📍 Thanks, address updated to: {address}",
        "hi": "📍 Shukriya, address update ho gaya: {address}",
    },
    "pay_link": {
        "en": "💳 Here's your payment link – ₹{paid:.0f} after ₹{d} off: {link}\nWe ship as soon as it's paid.",
        "hi": "💳 Yeh raha payment link – ₹{d} ki chhoot ke baad ₹{paid:.0f}: {link}\nPayment hote hi ship karenge.",
    },
    "paid": {
        "en": "🎉 Payment received (₹{paid:.0f}). Your order ships today!",
        "hi": "🎉 Payment mil gaya (₹{paid:.0f}). Aapka order aaj ship hoga!",
    },
    "cancelled_by_customer": {
        "en": "No problem – we've cancelled it. Hope to see you again! 💜",
        "hi": "Koi baat nahi – order cancel kar diya hai. Phir milenge! 💜",
    },
    "cancelled_timeout": {
        "en": "We couldn't reach you, so we've cancelled this Cash on Delivery order. Still want it? Pay online and we'll ship right away (₹{d} off): {link}",
        "hi": "Aapse baat nahi ho payi, isliye COD order cancel kar diya hai. Ab bhi chahiye? Online pay karein, turant bhej denge (₹{d} chhoot): {link}",
    },
    "reschedule": {
        "en": "Sure! We'll hold it and ship so it reaches you when you're available. ✅",
        "hi": "Bilkul! Hum order rok kar aisa bhejenge ki aapke time pe pahunche. ✅",
    },
    "agent": {
        "en": "Thanks! A team member will reply here shortly. 🙂",
        "hi": "Shukriya! Hamari team jaldi hi yahin jawab degi. 🙂",
    },
    "unclear": {
        "en": "Sorry, I didn't catch that. Reply YES to ship your order, NO to cancel, or PAY to pay online.",
        "hi": "Maaf kijiye, samajh nahi aaya. Order bhejne ke liye YES, cancel ke liye NO, online pay ke liye PAY likhiye.",
    },
}

BUTTONS = {
    "confirm": [("YES", "✅ Yes, ship it"), ("PAY", "💳 Pay online"), ("NO", "❌ Cancel")],
    "nudge": [("PAY", "💳 Pay online")],
    "hold": [("PAY", "💳 Pay online")],
    "reminder": [("YES", "✅ Yes"), ("NO", "❌ No")],
    "last_call": [("YES", "✅ Yes, ship it"), ("NO", "❌ Cancel")],
    "unclear": [("YES", "✅ Yes"), ("PAY", "💳 Pay online"), ("NO", "❌ No")],
}

# Final states and what the warehouse should do with the order.
FINAL = {
    "confirmed": "ship_cod", "paid": "ship_prepaid", "shipped_cod": "ship_cod",
    "cancelled": "cancel", "cancelled_no_reply": "cancel",
}


class MockWhatsApp:
    """Stands in for the WhatsApp Cloud API: records payloads instead of sending."""

    def __init__(self):
        self.outbox = []

    def send(self, phone, text, buttons=None):
        if buttons:
            payload = {"messaging_product": "whatsapp", "to": f"91{phone}", "type": "interactive",
                       "interactive": {"type": "button", "body": {"text": text},
                                       "action": {"buttons": [{"type": "reply", "reply": {"id": i, "title": t}}
                                                              for i, t in buttons[:3]]}}}
        else:
            payload = {"messaging_product": "whatsapp", "to": f"91{phone}", "type": "text", "text": {"body": text}}
        self.outbox.append(payload)
        return payload


class Conversation:
    _ids = itertools.count(1)

    def __init__(self, order, decision, policy, sender=None, use_llm=True):
        self.id = f"wa{next(self._ids)}"
        self.order = order
        self.decision = decision
        self.kind = decision["action"]
        if self.kind not in ("confirm", "nudge", "hold"):
            raise ValueError("ship-as-is orders don't need a WhatsApp flow")
        self.cfg = policy["whatsapp"]
        self.discount = policy["prepaid_nudge"]["discount_rupees"]
        self.llm_model = policy["llm"]["model"]
        self.use_llm = use_llm and policy["llm"]["enabled"]
        self.lang = "hi" if order["state"] in self.cfg["hinglish_states"] else "en"
        self.brand = self.cfg.get("brand_name", "our store")
        self.sender = sender or MockWhatsApp()
        self.now = order["order_datetime"]
        self.sent_at = None
        self.state = "scheduled"
        self.needs_address = decision["judgement"]["address_quality"] == "unusable"
        self.reminded = self.last_called = False
        self.last_call_at = None
        self.scheduled_for = self._next_send_time(self.now)
        self.unclear_count = 0
        self.agent_flag = False
        self.messages = []
        self._tick()

    # ------------------------------------------------------------------ helpers
    def _quiet(self, t):
        qs, qe = self.cfg["quiet_start"], self.cfg["quiet_end"]
        return t.hour >= qs or t.hour < qe

    def _next_send_time(self, t):
        """Push a send time out of quiet hours to the next morning."""
        if not self._quiet(t):
            return t
        qe = self.cfg["quiet_end"]
        day = t if t.hour < qe else t + timedelta(days=1)
        return day.replace(hour=qe, minute=0, second=0, microsecond=0)

    def _vars(self):
        o = self.order
        return {"first": o["customer_name"].split()[0].title() if o["customer_name"] else "there",
                "brand": self.brand, "items": ", ".join(o["items"]), "value": o["order_value"],
                "d": self.discount, "paid": max(o["order_value"] - self.discount, 0),
                "link": PAY_LINK.format(order_id=o["order_id"]), "address": o["address"],
                "left": max(self.cfg["timeout_hours"] - self.cfg["last_call_after_hours"], 1)}

    def _out(self, key, buttons_key=None, at=None):
        text = T[key][self.lang].format(**self._vars())
        buttons = BUTTONS.get(buttons_key or key)
        self.sender.send(self.order["phone"], text, buttons)
        self.messages.append({"at": (at or self.now).isoformat(), "dir": "out", "text": text,
                              "buttons": [b[1] for b in buttons] if buttons else []})

    def _note(self, text):
        self.messages.append({"at": self.now.isoformat(), "dir": "system", "text": text})

    def _hours_since_sent(self):
        return (self.now - self.sent_at).total_seconds() / 3600 if self.sent_at else 0

    @property
    def done(self):
        return self.state in FINAL

    @property
    def warehouse_action(self):
        return FINAL.get(self.state, "wait")

    # ------------------------------------------------------------------ clock
    def advance(self, hours):
        """Move the simulated clock forward, firing any reminders/timeouts on the way."""
        end = self.now + timedelta(hours=hours)
        while self.now < end and not self.done:
            self.now = min(end, self.now + timedelta(minutes=30))
            self._tick()
        self.now = end
        return self

    def _tick(self):
        if self.state == "scheduled":
            send_at = self.scheduled_for
            if self.now >= send_at:
                self.sent_at = send_at
                self.state = "awaiting"
                self._out(self.kind, at=send_at)
                if self.needs_address and self.kind == "confirm":
                    self._out("address_ask", at=send_at)
            elif not self.messages:
                self._note(f"Order placed during quiet hours – first message scheduled for {send_at:%d %b %H:%M}.")
            return

        h = self._hours_since_sent()
        if self.kind == "nudge":
            # A nudge never blocks the order for long: one message, then ship as COD.
            window = self.cfg.get("nudge_window_hours", 6)
            if self.state in ("awaiting", "awaiting_payment") and h >= window:
                self.state = "shipped_cod"
                self._note(f"No payment within {window}h – shipped as Cash on Delivery (no further messages).")
            return

        if self.state in ("awaiting", "awaiting_address", "awaiting_payment"):
            if self._quiet(self.now):
                return  # nothing is sent or cancelled at night; it all waits for the morning
            timeout = self.cfg["timeout_hours"]
            # Never cancel without a last-call message first: if the +20h last call fell in
            # quiet hours it goes out in the morning, and the deadline moves with it.
            grace = timedelta(hours=timeout - self.cfg["last_call_after_hours"])
            warned = self.kind != "confirm" or self.state != "awaiting" or (
                self.last_called and self.now - self.last_call_at >= grace)
            if h >= timeout and warned:
                if self.state == "awaiting_address":
                    self.state = "confirmed"
                    self._note("Customer confirmed but never sent a full address – shipped; courier will call.")
                elif self.state == "awaiting_payment" and self.kind == "confirm":
                    self.state = "confirmed"
                    self._note("Customer asked to pay online but didn't – they wanted the order, shipped as COD.")
                elif self.kind == "hold" or self.cfg["confirm_no_reply"] == "cancel":
                    self.state = "cancelled_no_reply"
                    self._out("cancelled_timeout")
                    self._note("No reply to the message, reminder or last call – COD cancelled; pay-online link left open.")
                else:
                    self.state = "confirmed"
                    self._note(f"No reply in {timeout}h – shipped anyway (policy: confirm_no_reply=ship).")
                return
            if self.kind == "confirm" and self.state == "awaiting":
                if not self.last_called and h >= self.cfg["last_call_after_hours"]:
                    self.last_called, self.last_call_at = True, self.now
                    self.reminded = True
                    self._out("last_call")
                elif not self.reminded and h >= self.cfg["reminder_after_hours"]:
                    self.reminded = True
                    self._out("reminder")

    # ------------------------------------------------------------------ inbound
    def receive(self, text):
        if self.done:
            self.messages.append({"at": self.now.isoformat(), "dir": "in", "text": text})
            self._note("Conversation already closed – routed to support inbox.")
            return {"intent": "closed"}
        self.messages.append({"at": self.now.isoformat(), "dir": "in", "text": text})
        if self.state == "scheduled":
            # Customer wrote to us before our (quiet-hours delayed) message went out:
            # they're awake, so send it now and then read their reply.
            self.sent_at, self.state = self.now, "awaiting"
            self._out(self.kind)
            if self.needs_address and self.kind == "confirm":
                self._out("address_ask")
        parsed = parse_reply(text, model=self.llm_model, use_llm=self.use_llm)
        intent = parsed["intent"]
        self._note(f"Read as: {intent} (via {parsed.get('source')})")

        if intent == "language_switch":
            self.lang = "hi" if parsed.get("language", "").lower().startswith("hi") else "en"
            self._out(self.kind)
            return parsed

        if intent == "address_update":
            new = parsed.get("new_address") or text
            j = judge_order(self.order["customer_name"], new, self.order["city"], self.order["pincode"],
                            model=self.llm_model, use_llm=self.use_llm)
            if j["address_quality"] == "unusable":
                self._out("address_ask")
                return parsed
            self.order = {**self.order, "address": new}
            self.needs_address = False
            self._out("address_thanks")
            if self.kind == "confirm" or self.state == "awaiting_address":
                # Taking the trouble to fix the address is as good as a yes.
                self.state = "confirmed"
                self._out("confirmed")
            return parsed

        if intent in ("yes", "reschedule"):
            if self.kind == "hold":
                # COD isn't on the table for held orders – offer the link again.
                self.state = "awaiting_payment"
                self._out("pay_link")
            elif self.kind == "nudge":
                self.state = "shipped_cod"
                self._out("confirmed")
            elif self.needs_address:
                self.state = "awaiting_address"
                self._out("address_ask")
            else:
                self.state = "confirmed"
                self._out("reschedule" if intent == "reschedule" else "confirmed")
            return parsed

        if intent == "no":
            if self.kind == "nudge":
                # "No" to a discount offer just means "I'll pay cash".
                self.state = "shipped_cod"
                self._out("confirmed")
            else:
                self.state = "cancelled"
                self._out("cancelled_by_customer")
            return parsed

        if intent == "pay_online":
            self.state = "awaiting_payment"
            self._out("pay_link")
            return parsed

        if intent == "question":
            self.agent_flag = True
            self._out("agent")
            return parsed

        # unclear
        self.unclear_count += 1
        if self.unclear_count >= 2:
            self.agent_flag = True
            self._out("agent")
        else:
            self._out("unclear")
        return parsed

    def payment_received(self):
        if self.done and self.state != "cancelled_no_reply":
            return
        self.state = "paid"
        self.order = {**self.order, "payment_method": "PREPAID"}
        self._out("paid")
        if self.needs_address:
            self._out("address_ask")

    def to_dict(self):
        return {"id": self.id, "order_id": self.order["order_id"], "kind": self.kind, "lang": self.lang,
                "state": self.state, "warehouse_action": self.warehouse_action, "now": self.now.isoformat(),
                "sent_at": self.sent_at.isoformat() if self.sent_at else None,
                "hours_since_sent": round(self._hours_since_sent(), 1), "agent_flag": self.agent_flag,
                "needs_address": self.needs_address, "messages": self.messages,
                "customer": {"name": self.order["customer_name"], "phone": self.order["phone"],
                             "city": self.order["city"], "address": self.order["address"]},
                "decision": {k: self.decision[k] for k in ("action_label", "risk_pct", "reason")}}
