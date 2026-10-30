"""Tiny stdlib web server: dashboard + order checker + rules editor + WhatsApp simulator.

    python3 -m rto.server            # http://localhost:8077

There is no live Shopify feed in this demo, so "yesterday" means a day from the
September orders (the month the model never trained on), replayed through the engine.
"""
import copy
import json
import os
import threading
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import backtest
from .data import load_orders, parse_order, split_by_month
from .economics import ACTIONS
from .engine import LABELS, MODEL_PATH, ROOT, Engine, load_policy, save_policy
from .judge import llm_available
from .whatsapp import Conversation

HOLDOUT = "2026-09"
WEB = os.path.join(ROOT, "web")


class State:
    def __init__(self):
        if not os.path.exists(MODEL_PATH):
            raise SystemExit("No model yet - run: python3 scripts/train.py")
        self.lock = threading.Lock()
        self.engine = Engine()
        orders, self.quality = load_orders(os.path.join(ROOT, "data", "orders_sample.csv"))
        _, test = split_by_month(orders, HOLDOUT)
        self.prepared = backtest.prepare(test, self.engine)
        self.conversations = {}
        self.adhoc = {}
        self.replay()

    def replay(self):
        decisions, self.summary = backtest.run(self.prepared, self.engine)
        self.by_id = {d["order_id"] + d["order"]["order_datetime"].isoformat(): d for d in decisions}
        self.by_date = defaultdict(list)
        for d in decisions:
            self.by_date[d["order"]["order_datetime"].strftime("%Y-%m-%d")].append(d)

    def find(self, key):
        return self.by_id.get(key) or self.adhoc.get(key)


def money(d, field="impact"):
    return {k: round(d[field][k]) for k in ("saved", "lost", "cost", "net")}


def public(d):
    o = d["order"]
    return {"key": d["order_id"] + o["order_datetime"].isoformat(), "order_id": d["order_id"],
            "time": o["order_datetime"].strftime("%d %b %H:%M"), "customer": o["customer_name"],
            "city": o["city"], "value": o["order_value"], "items": o["items"],
            "action": d["action"], "action_label": d["action_label"], "risk_pct": d["risk_pct"],
            "reason": d["reason"], "first_time": d["first_time"], "impact": money(d),
            "actual": d.get("actual"), "address": o["address"],
            "judgement_source": d["judgement"].get("source")}


def day_summary(decisions):
    s = {"orders": len(decisions), "buckets": {a: 0 for a in ACTIONS},
         "expected": defaultdict(float), "actual": defaultdict(float)}
    for d in decisions:
        s["buckets"][d["action"]] += 1
        for k, v in d["impact"].items():
            if k in ("saved", "lost", "cost", "net"):
                s["expected"][k] += v
        for k, v in d.get("impact_actual", {}).items():
            s["actual"][k] += v
    s["expected"] = {k: round(v) for k, v in s["expected"].items()}
    s["actual"] = {k: round(v) for k, v in s["actual"].items()}
    return s


class Handler(BaseHTTPRequestHandler):
    state: State = None

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        st = self.state
        if u.path in ("/", "/index.html"):
            with open(os.path.join(WEB, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/meta":
            dates = sorted(st.by_date)
            self._json({"dates": dates, "latest": dates[-1], "llm": llm_available(),
                        "llm_enabled": st.engine.policy["llm"]["enabled"], "model": st.engine.model.meta,
                        "labels": LABELS, "quality": st.quality.__dict__})
        elif u.path == "/api/day":
            with st.lock:
                date = q.get("date") or max(st.by_date)
                decs = st.by_date.get(date, [])
                days = [{"date": d, **day_summary(st.by_date[d])["expected"],
                         "actual_net": day_summary(st.by_date[d])["actual"].get("net", 0)}
                        for d in sorted(st.by_date) if d <= date][-30:]
                self._json({"date": date, "summary": day_summary(decs),
                            "orders": [public(d) for d in sorted(decs, key=lambda d: -d["risk_pct"])],
                            "trend": days, "month": {"buckets": st.summary["buckets"],
                                                     "expected": st.summary["expected"],
                                                     "actual": st.summary["actual"],
                                                     "orders": st.summary["orders"]}})
        elif u.path == "/api/policy":
            self._json(st.engine.policy)
        elif u.path.startswith("/api/wa/"):
            c = st.conversations.get(u.path.split("/")[3])
            self._json(c.to_dict() if c else {"error": "not found"}, 200 if c else 404)
        elif u.path == "/api/wa":
            self._json([c.to_dict() for c in st.conversations.values()])
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        st = self.state
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._json({"error": "bad JSON"}, 400)

        if u.path == "/api/decide":
            row = {"order_id": "NEW" + datetime.now().strftime("%H%M%S"),
                   "order_datetime": datetime.now().strftime("%Y-%m-%d %H:%M"), **body}
            try:
                order = parse_order(row)
            except (KeyError, ValueError) as e:
                return self._json({"error": f"bad order: {e}"}, 400)
            with st.lock:
                d = st.engine.decide(order)
                d["order"] = order
                st.adhoc[d["order_id"] + order["order_datetime"].isoformat()] = d
            return self._json(public(d) | {"risk_up": d["risk_up"], "risk_down": d["risk_down"],
                                           "judgement": d["judgement"], "rule": d["rule"],
                                           "money_best_action": d["money_best_action"]})

        if u.path in ("/api/policy", "/api/policy/preview"):
            merged = copy.deepcopy(st.engine.policy)
            for sect, vals in body.items():
                if sect in merged and isinstance(vals, dict):
                    merged[sect].update(vals)
            t = merged["thresholds"]
            on = [t[k] for k in ("nudge_from", "confirm_from", "hold_from") if t[k] < 101]  # 101 = step off
            if any(v < 0 for v in on) or on != sorted(on):
                return self._json({"error": "thresholds must go up: nudge ≤ confirm ≤ hold (101 switches a step off)"}, 400)
            with st.lock:
                if u.path == "/api/policy":
                    save_policy(merged)
                    st.engine.reload_policy(load_policy())
                    st.replay()
                    return self._json({"ok": True, "month": st.summary})
                saved = copy.deepcopy(st.engine.policy)
                _, s = backtest.run(st.prepared, st.engine, merged)
                st.engine.reload_policy(saved)
                return self._json({"month": s})

        if u.path == "/api/wa/start":
            with st.lock:
                d = st.find(body.get("key", ""))
                if not d:
                    return self._json({"error": "unknown order"}, 404)
                if d["action"] == "ship":
                    return self._json({"error": "this order ships as-is, no WhatsApp needed"}, 400)
                o = d["order"]
                if body.get("as_now"):
                    o = {**o, "order_datetime": datetime.now().replace(second=0, microsecond=0)}
                c = Conversation(o, d, st.engine.policy)
                st.conversations[c.id] = c
                return self._json(c.to_dict())

        if u.path.startswith("/api/wa/"):
            parts = u.path.split("/")
            c = st.conversations.get(parts[3]) if len(parts) > 4 else None
            if not c:
                return self._json({"error": "not found"}, 404)
            with st.lock:
                if parts[4] == "reply":
                    c.receive(str(body.get("text", ""))[:500])
                elif parts[4] == "advance":
                    c.advance(float(body.get("hours", 1)))
                elif parts[4] == "pay":
                    c.payment_received()
                return self._json(c.to_dict())

        self._json({"error": "not found"}, 404)


def main():
    port = int(os.environ.get("PORT", 8077))
    print("loading orders and scoring September...")
    Handler.state = State()
    print(f"LLM: {'Claude (' + Handler.state.engine.policy['llm']['model'] + ')' if llm_available() else 'off - using offline heuristics (set ANTHROPIC_API_KEY to enable)'}")
    print(f"open http://localhost:{port}")
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), port), Handler).serve_forever()


if __name__ == "__main__":
    main()
