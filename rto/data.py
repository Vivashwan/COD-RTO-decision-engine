"""Load and clean the historical COD orders export.

The raw file has real-world mess in it (duplicate rows, inconsistent status
labels, phone numbers in three formats, missing pincodes). Everything here is
plain code: none of it needs an LLM.
"""
import csv
import re
from dataclasses import dataclass, field
from datetime import datetime

FINAL_STATUSES = {"DELIVERED", "RTO"}


@dataclass
class QualityReport:
    raw_rows: int = 0
    exact_duplicates_removed: int = 0
    non_final_status_removed: int = 0
    status_labels_fixed: int = 0
    phones_reformatted: int = 0
    phones_invalid: int = 0
    pincodes_missing: int = 0
    zero_value_orders: int = 0
    prior_rto_exceeds_prior_orders: int = 0
    notes: list = field(default_factory=list)


def normalise_phone(raw):
    """Return (10-digit phone or None, was_reformatted)."""
    digits = re.sub(r"\D", "", raw or "")
    reformatted = digits != (raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits, reformatted
    return None, reformatted


def parse_order(row):
    """Turn a raw CSV/API row into a typed order dict. Works for new orders too
    (no final_status needed)."""
    phone, _ = normalise_phone(row.get("phone", ""))
    prior_orders = int(row.get("customer_prior_orders") or 0)
    prior_rto = int(row.get("customer_prior_rto") or 0)
    pincode = (row.get("pincode") or "").strip()
    return {
        "order_id": (row.get("order_id") or "").strip(),
        "order_datetime": datetime.strptime(row["order_datetime"].strip(), "%Y-%m-%d %H:%M")
        if row.get("order_datetime") else datetime.now(),
        "customer_name": (row.get("customer_name") or "").strip(),
        "phone_raw": (row.get("phone") or "").strip(),
        "phone": phone,
        "address": (row.get("address") or "").strip(),
        "city": (row.get("city") or "").strip(),
        "state": (row.get("state") or "").strip(),
        "pincode": pincode if re.fullmatch(r"[1-9]\d{5}", pincode) else "",
        "order_value": float(row.get("order_value") or 0),
        "items": [i for i in (row.get("items") or "").split("|") if i],
        "num_items": int(row.get("num_items") or 1),
        "customer_prior_orders": prior_orders,
        # A customer can't have refused more orders than they placed; treat as data error.
        "customer_prior_rto": min(prior_rto, prior_orders),
        "utm_source": (row.get("utm_source") or "").strip(),
        "courier": (row.get("courier") or "").strip(),
        "payment_method": (row.get("payment_method") or "COD").strip().upper(),
        "final_status": (row.get("final_status") or "").strip().upper() or None,
    }


def load_orders(path):
    """Load the historical export. Returns (orders, QualityReport)."""
    q = QualityReport()
    seen = set()
    orders = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            q.raw_rows += 1
            key = tuple(row.values())
            if key in seen:
                q.exact_duplicates_removed += 1
                continue
            seen.add(key)

            status = row["final_status"].strip().upper()
            if status != row["final_status"]:
                q.status_labels_fixed += 1
            if status not in FINAL_STATUSES:
                q.non_final_status_removed += 1
                continue

            phone, reformatted = normalise_phone(row["phone"])
            if phone and reformatted:
                q.phones_reformatted += 1
            if not phone:
                q.phones_invalid += 1
            if not row["pincode"].strip():
                q.pincodes_missing += 1
            if float(row["order_value"] or 0) <= 0:
                q.zero_value_orders += 1
            if int(row["customer_prior_rto"] or 0) > int(row["customer_prior_orders"] or 0):
                q.prior_rto_exceeds_prior_orders += 1
            orders.append(parse_order(row))
    return orders, q


def split_by_month(orders, holdout_month):
    """Out-of-time split: train on everything before holdout_month ('YYYY-MM')."""
    train = [o for o in orders if o["order_datetime"].strftime("%Y-%m") < holdout_month]
    test = [o for o in orders if o["order_datetime"].strftime("%Y-%m") == holdout_month]
    return train, test
