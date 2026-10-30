# COD Order Guard

Looks at every Cash-on-Delivery order before it ships and decides one of:

| Action | When (default rules) | What happens |
|---|---|---|
| **Ship as-is** | risk of return < 25% | ships today |
| **Nudge to prepaid** | 25–50% | WhatsApp offers ₹40 off to pay online; ships as COD after 6 h if they don't |
| **Ship after WhatsApp confirmation** | 50–75%, unusable address, or a likely double order | customer must reply YES (and send a full address if needed); no reply after reminder + last call → cancelled with a pay-online link |
| **Hold / cancel COD** | ≥ 75% (repeat buyers only), fake name **and** junk address, or 2+ past refusals | COD is not offered; customer gets a pay-online link |

Every decision comes with a plain-English reason and its ₹ effect, for example:

> *Rule: fake-looking name AND unusable address. 91% chance of return: address "xyz" is too vague for a courier to find; Darbhanga (846xxx): 61% of past COD orders came back (57 orders). Don't ship COD. WhatsApp offers a pay-online link instead.*

**Deliverables:** [business memo](docs/MEMO.md) ([PDF](docs/MEMO.pdf)) · [decision log](docs/DECISION_LOG.md) · [AI usage log](docs/AI_USAGE_LOG.md) · [all numbers, reproducible](reports/findings.md)

## Run it (≈ 2 minutes, no installs)

Needs Python 3.10+. Nothing else.

```bash
git clone https://github.com/Vivashwan/COD-RTO-decision-engine.git && cd COD-RTO-decision-engine
python3 -m rto.server                 # → open http://localhost:8077
```

The trained model is committed, so that's all you need. The page has four tabs:

1. **Morning report**: the founder's screen. It shows yesterday's orders in the 4 buckets, ₹ saved from avoided RTOs, ₹ lost on orders that were probably genuine, net ₹, a 30-day trend, and every decision with its reason. There's no live Shopify feed here, so "yesterday" is any day of **September 2026**, the month the model never saw during training, replayed through the engine. Each ₹ figure is shown twice: as the live system would estimate it, and against what really happened to those orders.
2. **Check an order**: type in any order and see the decision, the reasons, and the ₹ maths.
3. **WhatsApp simulator**: a mock of the confirm / nudge / hold flows. Reply as the customer ("haan bhej do", "cancel", "H.No 12, Shastri Nagar…", anything) and move the clock forward to see reminders, quiet hours and the 24-hour timeout.
4. **Rules**: change thresholds, the discount, rules and assumptions with no code. *Preview* replays September with your numbers. *Save* writes [`config/policy.json`](config/policy.json).

Port busy? Use `PORT=9000 python3 -m rto.server`.

### Optional: switch on the LLM

Without a key, address/name checks and free-text replies use built-in rules. That's fine for this dataset, and it's what all the numbers in the memo use. To let Claude do those two jobs:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python scripts/train.py      # re-judges addresses with Claude (cached) and retrains
.venv/bin/python -m rto.server
```

The header pill shows `LLM: Claude on`. If calls fail, it falls back to the rules automatically.

### Other commands

```bash
python3 -m unittest discover -s tests   # 25 tests, offline, < 1 s
python3 scripts/train.py                # retrain on Apr–Aug, report September AUC + calibration (~10 s)
python3 scripts/train.py --all          # train on all 6 months (what you'd deploy)
python3 scripts/analyse.py              # regenerate reports/findings.md, every number in the memo (~20 s)
```

## How it works

```
order ──► judge.py ───────────► model.py ──────────► engine.py ─────────────────► whatsapp.py
          LLM (or rules):       district RTO history   risk % → thresholds          confirm / nudge / hold
          address usable?       + customer history     → hard rules                 flows, mock sender,
          name real?            + order features       → reason + ₹ (economics.py)  quiet hours, timeouts
```

| File | What it does |
|---|---|
| [`rto/data.py`](rto/data.py) | Loads and cleans the CSV: duplicate rows, status spellings, `+91` phones, missing pincodes. |
| [`rto/judge.py`](rto/judge.py) | **The only LLM use.** Judges address usability (Hinglish/landmark-aware) and name plausibility, and reads free-text WhatsApp replies. Rule-based fallback and on-disk cache included. |
| [`rto/model.py`](rto/model.py) | Pure-Python logistic regression on 13 readable features. District (pincode-prefix) RTO history is smoothed toward the state and computed out-of-fold. |
| [`rto/economics.py`](rto/economics.py) | ₹ maths for each action, using only numbers from the brief plus clearly labelled assumptions. |
| [`rto/engine.py`](rto/engine.py) | Turns risk into a decision: thresholds, then hard rules, then a plain-English reason. |
| [`rto/whatsapp.py`](rto/whatsapp.py) | Conversation state machine with English and Hinglish templates and a mock WhatsApp Cloud API sender. |
| [`rto/backtest.py`](rto/backtest.py), [`scripts/analyse.py`](scripts/analyse.py) | Replay history through any policy and add up the ₹. |
| [`rto/server.py`](rto/server.py), [`web/index.html`](web/index.html) | Standard-library HTTP server and a single-page dashboard. |

### Where the LLM is used, and where it isn't

| Job | Who does it | Why |
|---|---|---|
| Is this address findable? ("ph krke aana, Hanuman Mandir ke saamne" is fine; "call me" is not) | **LLM** | Language understanding. Landmark/Hinglish addresses are normal in India, and regexes can't tell those apart from junk once you leave this dataset's patterns. |
| Is "Na Yeon" a real name and "asdf" a fake one? | **LLM** | Same reason. |
| What did "bhaiya parso bhejna" / "galat address hai, H.No 12…" mean? | **LLM**, after a keyword pass | Button taps and "yes/haan/no" never reach the LLM. Only free text does. |
| Phone and pincode validity, duplicates, customer history | code | Exact rules, free, deterministic. |
| Risk probability | logistic regression | Needs calibrated probabilities and per-feature reasons; an LLM gives neither reliably. |
| Thresholds, discount, ₹ impact | arithmetic + config | Must be auditable and editable by ops. |
| The reason text | templates | Built from the model's actual drivers. An LLM could word it more smoothly but might state a reason the model didn't use. |

## Data notes

`data/orders_sample.csv` has 2,018 rows, which become 1,987 usable orders after removing 30 exact duplicates and 1 order still in transit. Status labels were normalised, `+91` phones reformatted, and 9-digit phones flagged as invalid. Details are in [reports/findings.md](reports/findings.md#data-quality-fixed-before-anything-else).
