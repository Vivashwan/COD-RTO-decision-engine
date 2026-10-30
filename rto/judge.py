"""The only place the system uses an LLM.

Two jobs where language understanding beats rules:
  1. judge_order()  - is this address something a courier can find? is the name
                      a real person or keyboard mash? (Hinglish / landmark-style
                      addresses are normal in India and must NOT be penalised.)
  2. parse_reply()  - what did the customer mean in a free-text WhatsApp reply
                      ("haan bhej do", "kal aana", "galat address hai, H.No 12...")?

Everything numeric (risk score, ₹ maths, thresholds) stays in plain code.

If ANTHROPIC_API_KEY is not set, the `anthropic` package is missing, or the call
fails, both functions fall back to deterministic heuristics so the system keeps
running. Results are cached on disk so each address is judged once.
"""
import hashlib
import json
import os
import re
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(ROOT, "data", "judge_cache.json")
DEFAULT_MODEL = "claude-opus-5-5"

ADDRESS_QUALITIES = ("complete", "landmark_only", "unusable")
REPLY_INTENTS = ("yes", "no", "pay_online", "address_update", "reschedule",
                 "question", "language_switch", "unclear")

_cache_lock = threading.Lock()
_cache = None
_llm_failures = 0
MAX_LLM_FAILURES = 3  # after this many failures in a row, stop trying for this process


# ---------------------------------------------------------------- cache

def _load_cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
    return _cache


def _save_cache():
    with _cache_lock:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=0)
        os.replace(tmp, CACHE_PATH)


def _key(*parts):
    return hashlib.sha1("\x1f".join(p.strip().lower() for p in parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- heuristics

KEYBOARD_MASH = {"asdf", "qwerty", "test", "abc", "xyz", "aaa", "na", "n/a", "none",
                 "test order", "xyz xyz", "abcd", "asd", "zzz", "customer", "name"}
VAGUE_ADDRESS = {".", "..", "-", "xyz", "abc", "home", "house", "ghar", "call me", "call",
                 "near station", "na", "n/a", "address", "same", "test"}
HOUSE_NO = re.compile(
    r"(\b(house\s*no|h\.?\s*no|flat|plot|door|shop|room|qtr|quarter)\b\.?\s*[:#-]?\s*[A-Z]?-?\d)"
    r"|(\b\d+\s*/\s*\d+)|(\b[A-Z]-\s?\d+)|(#\s?\d+)", re.I)
AREA_WORDS = re.compile(
    r"\b(sector|road|rd|marg|nagar|colony|society|apartments?|enclave|vihar|layout|street|st|"
    r"lane|gali|mohalla|ward|village|vill|post|block|phase|extension|park|chowk|bazaar|main)\b", re.I)
LANDMARK = re.compile(
    r"\b(near|opp\.?|opposite|behind|beside|ke\s+(paas|saamne|peeche|andar|bagal)|"
    r"se\s+(left|right|aage)|wali\s+gali|ghar\s+ka\s+naam|niwas|mandir|masjid|school|bank|"
    r"petrol\s+pump|water\s+tank|hospital|station|bazaar|chowk)\b", re.I)


def heuristic_judge(name, address, city):
    issues = []
    a = re.sub(r"\s+", " ", address or "").strip()
    a_low = a.lower().strip(" .,")
    words = re.findall(r"[A-Za-z]{2,}", a)

    if (not a or a_low in VAGUE_ADDRESS or a_low == (city or "").lower()
            or len(a) < 12 or len(words) < 2):
        quality = "unusable"
        issues.append("too vague for a courier to find" if a else "address is empty")
    elif HOUSE_NO.search(a) and (AREA_WORDS.search(a) or len(words) >= 3):
        quality = "complete"
    elif LANDMARK.search(a) or len(words) >= 3:
        quality = "landmark_only"
        issues.append("no house/flat number, only a landmark")
    else:
        quality = "unusable"
        issues.append("no house number and no recognisable landmark")

    n = re.sub(r"\s+", " ", name or "").strip()
    n_low = n.lower()
    tokens = n_low.split()
    plausible = not (
        not tokens
        or all(t in KEYBOARD_MASH for t in tokens)
        or "test" in tokens
        or re.search(r"\d", n)
        or re.fullmatch(r"(.)\1+", n_low.replace(" ", ""))
    )
    if not plausible:
        issues.append(f"name '{n}' does not look like a real person")

    return {"address_quality": quality, "name_plausible": plausible,
            "issues": issues, "source": "heuristic"}


YES_WORDS = {"yes", "y", "haan", "ha", "han", "haa", "ji", "haan ji", "ok", "okay", "confirm",
             "confirmed", "theek hai", "thik hai", "sure", "bhej do", "bhejo", "1", "👍", "✅"}
NO_WORDS = {"no", "n", "nahi", "nahin", "nai", "na", "cancel", "mat bhejo", "nahi chahiye",
            "don't want", "dont want", "2", "❌"}
PAY_WORDS = {"pay", "pay online", "online", "upi", "prepaid", "pay now", "3"}


def heuristic_reply(text):
    t = re.sub(r"[^\w\s👍✅❌]", " ", (text or "").lower()).strip()
    t = re.sub(r"\s+", " ", t)
    if t in YES_WORDS:
        return {"intent": "yes", "source": "keyword"}
    if t in NO_WORDS:
        return {"intent": "no", "source": "keyword"}
    if t in PAY_WORDS:
        return {"intent": "pay_online", "source": "keyword"}
    if t in {"hindi", "english"}:
        return {"intent": "language_switch", "language": t, "source": "keyword"}
    if HOUSE_NO.search(text or "") and len(t.split()) >= 3:
        return {"intent": "address_update", "new_address": (text or "").strip(), "source": "keyword"}
    if re.search(r"\b(cancel|nahi chahiye|mat bhej\w*|don ?t want|nahi|nahin|no)\b", t):
        return {"intent": "no", "source": "keyword"}
    if (len(t.split()) <= 4 and not re.search(r"\?|\b(kab|kitna|kya|when|how|why)\b", text or "")
            and re.search(r"\b(haan|han|haa|ha|ji|yes|ok|okay|theek|thik|bhej\w*|confirm\w*|sure)\b", t)):
        return {"intent": "yes", "source": "keyword"}
    return {"intent": "unclear", "source": "heuristic"}


# ---------------------------------------------------------------- LLM

def _client():
    if (not os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("RTO_DISABLE_LLM")
            or _llm_failures >= MAX_LLM_FAILURES):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic()


def _llm_json(system, user, schema, model):
    global _llm_failures
    client = _client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:  # network/auth/rate-limit: the heuristic takes over
        _llm_failures += 1
        print(f"[judge] LLM call failed, using heuristic: {type(e).__name__}: {e}")
        if _llm_failures == MAX_LLM_FAILURES:
            print("[judge] too many LLM failures - using heuristics for the rest of this run")
        return None
    _llm_failures = 0
    if resp.stop_reason != "end_turn":
        return None
    text = next((b.text for b in resp.content if b.type == "text"), None)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


JUDGE_SYSTEM = """You check Cash-on-Delivery orders for an Indian D2C haircare brand before they ship.
For each order decide two things:

1. address_quality - could a local courier find this door?
   - "complete": has a house/flat/plot/door number (or equivalent) AND a street/area/society.
   - "landmark_only": no door number but a real, specific landmark or directions a local courier
     could follow, e.g. "Station chowk se left, Hanuman Mandir ke saamne" or
     "near SBI Bank, ghar ka naam Om niwas". Hinglish, Hindi in Roman script and landmark-style
     directions are NORMAL in India and are not a problem in themselves.
   - "unusable": a courier could not find it - placeholders (".", "xyz", "home", "call me"),
     only the city name, or a single generic landmark like "near station".
2. name_plausible - false only for clear junk ("asdf", "qwerty", "test", "abc", "xyz xyz", "na",
   repeated letters). Single first names, initials and uncommon Indian names are plausible.

List short plain-English issues an operations person would understand (max 3)."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "address_quality": {"type": "string", "enum": list(ADDRESS_QUALITIES)},
        "name_plausible": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["address_quality", "name_plausible", "issues"],
    "additionalProperties": False,
}

REPLY_SYSTEM = """You read a customer's WhatsApp reply to a message from an Indian haircare brand.
The message asked them to confirm their Cash-on-Delivery order (and may have offered a discount
to pay online instead). Replies can be English, Hindi, Hinglish or emoji.
Classify the intent:
- yes: wants the order (e.g. "haan bhej do", "ok", "👍")
- no: does not want it / cancel
- pay_online: wants to pay online / UPI
- address_update: is giving a corrected or fuller address -> put it in new_address
- reschedule: wants delivery later ("kal aana", "next week")
- question: asks something (price, delivery time, product)
- language_switch: asks for Hindi or English -> put "hindi" or "english" in language
- unclear: anything else
Use empty strings for new_address/language when not applicable."""

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(REPLY_INTENTS)},
        "new_address": {"type": "string"},
        "language": {"type": "string"},
    },
    "required": ["intent", "new_address", "language"],
    "additionalProperties": False,
}


def judge_order(name, address, city, pincode="", model=DEFAULT_MODEL, use_llm=True):
    cache = _load_cache()
    k = "order:" + _key(name, address, city, pincode)
    if k in cache and (cache[k]["source"] == "llm" or not use_llm or _client() is None):
        return cache[k]

    result = None
    if use_llm:
        out = _llm_json(JUDGE_SYSTEM,
                        f"Name: {name}\nAddress: {address}\nCity: {city}\nPincode: {pincode or '(missing)'}",
                        JUDGE_SCHEMA, model)
        if out and out.get("address_quality") in ADDRESS_QUALITIES:
            result = {**out, "source": "llm"}
    if result is None:
        result = heuristic_judge(name, address, city)

    with _cache_lock:
        cache[k] = result
    if result["source"] == "llm":
        _save_cache()
    return result


def parse_reply(text, model=DEFAULT_MODEL, use_llm=True):
    # Button taps and one-word answers never need the LLM.
    quick = heuristic_reply(text)
    if quick["intent"] != "unclear" or not use_llm:
        return quick
    out = _llm_json(REPLY_SYSTEM, f"Customer reply: {text}", REPLY_SCHEMA, model)
    if out and out.get("intent") in REPLY_INTENTS:
        out = {k: v for k, v in out.items() if v != ""}
        return {**out, "source": "llm"}
    return quick


def llm_available():
    return _client() is not None


def flush_cache():
    _load_cache()
    _save_cache()
