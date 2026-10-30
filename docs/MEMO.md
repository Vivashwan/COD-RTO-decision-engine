# What to do with risky COD orders

**To:** Founder · **Re:** RTO on Cash-on-Delivery orders · **Date:** 31 October 2026

## The short version

- **Don't auto-cancel risky COD orders.** On September's orders, the proposed "cancel anything risky, stop COD in bad areas" rule would have cancelled 71 of 329 orders. **33 of those would have been delivered**, 28 of them to first-time buyers. Net effect: **−₹9,400 for the month, about −₹47,000 a month** at our volume.
- **Instead, sort every COD order into four lanes.** Ship 55% untouched, offer ₹40 off to pay online to the next 30%, WhatsApp-confirm the riskiest 12%, and hold about 3%. Replayed on September, this **prevents roughly 1 in 4 RTOs while losing about 2 genuine orders: +₹3,100 for the month, about +₹15,500 a month**, and it stays positive even if WhatsApp confirmation works much worse than I've assumed.
- **Why cancelling is the wrong tool:** a delivered ₹649 order earns us **₹337**, and an RTO costs **₹169**. Cancelling only pays when we're more than **67–71% sure** an order will fail. The average COD order is at 28%.

## 1. What surprised me in the data (≈2,000 past COD orders, Apr–Sep)

1. **"Bad pincodes" don't exist at our volume, but bad districts do.** 554 of our 1,066 pincodes have exactly one order, so a pincode's "history" is usually one coin flip. One level up, the pattern is stark: Gorakhpur 64% RTO, Darbhanga 61%, Bareilly 61%, against Pune and Mumbai at 18%. But even Gorakhpur delivers 36% of its orders, so switching COD off there loses more margin than it saves.
2. **"Bad" orders mostly aren't fraud.** Orders with junk addresses (".", "xyz", "call me") return 62% of the time, which means **38% still get delivered**. Hinglish landmark addresses ("ph krke aana, Govt School ke paas") return at 31%, barely above average; that's simply how much of India writes addresses. And of the 8 orders with fake-looking names ("asdf", "test") but a proper address, **none** came back.
3. **A customer's own history beats everything else.** Repeat customers who never refused: 14% RTO. Refused once: 38%. **Refused twice or more: 73%**, the only big group where cancelling pays.
4. **Big COD baskets and Meta-ad first orders are the riskiest.** ₹1,500+ COD orders return 39% (vs 27%). First orders from Meta ads return 34%, against 22% for people who come to the site directly. So Marketing is right that new buyers are the growth engine, and they're also where the RTO is: first-time buyers account for 80% of all returns.
5. **Some losses are just plumbing.** 28 orders were double-submitted minutes apart and both copies shipped; 17 phone numbers were too short to be real.

## 2. Recommended rules, and what they'd have done to September

A model scores each order's chance of coming back. It learns from the area's history, the customer's history, whether the address is findable (judged by AI, which reads Hinglish), basket size and channel. It never saw September, so this is an honest test.

| Lane | When | Sept orders | Really came back |
|---|---|---|---|
| **Ship as-is** | risk under 25% | 180 (55%) | 11% |
| **Nudge to prepaid**: "pay online, get ₹40 off"; ships as COD after 6h if ignored | 25–50% | 99 (30%) | 37% |
| **WhatsApp confirm**: must reply YES; asks for a full address if needed; no reply after reminder + last call → cancelled, with a pay-online link | 50–75%, junk address, or double order | 41 (12%) | 51% |
| **Hold**: no COD, pay-online link only | 75%+ (repeat buyers only), fake name *and* junk address, or 2+ past refusals | 9 (3%) | 89% |

**Against shipping everything, for September:** RTO costs avoided ₹5,344 · margin given up on genuine orders −₹1,499 · discounts and WhatsApp −₹752 · **net +₹3,093**. That's ≈ **₹15,500 a month** at 1,650 COD orders, or about ₹1.9 lakh a year. It reaches 92% of what perfect order-by-order ₹ maths would get, with four lanes anyone can understand.

**Why ₹40 off for prepaid.** Moving a buyer at 25% risk to prepaid is worth at most about ₹49 to us; at 35% risk, ₹79; at 50%, ₹122. At 15% risk it's worth only ₹20, because those buyers would mostly pay anyway. So ₹40 (~6% of an average order) pays for itself exactly where the nudge lane starts, and a flat amount reads clearly in a WhatsApp message.

## 3. Balancing Operations and Marketing, in ₹

| Approach | Orders stopped | Genuine orders lost | Net for September | Per month (approx.) |
|---|---|---|---|---|
| Ship everything (today) | 0 | 0 | ₹0 | ₹0 |
| Ops: cancel at 40%+ risk, stop COD in the 4 worst districts | 71 | 33 | −₹9,431 | −₹47,000 |
| **Recommended** | 9 held, 41 confirm | ≈ 2 | **+₹3,093** | **+₹15,500** |

- **Operations is right about the problem.** RTO costs us about ₹78,000 a month today. They're wrong about the tool: most "risky" orders still deliver, and a delivered order is worth twice what an RTO costs.
- **Marketing is right about first-time buyers, for a slightly different reason.** The ₹300 ad cost is already spent by the time we decide. What a cancellation actually throws away is that order's ₹337 margin plus roughly ₹75 of future orders. So the rules include Marketing's protection: **the risk score alone can never cancel a first-time buyer; the most it can do is ask them to confirm.** In September that protection cost ₹106 (about ₹500 a month). I think that's worth paying.
- **Ops still gets hard stops where cancelling clearly pays:** two or more past refusals, or a fake name with a junk address. Even then the customer gets a pay-online link, so a held order isn't automatically a lost customer.

## 4. What could go wrong, and how we'd notice

- **WhatsApp confirmation works worse than assumed.** I assumed it catches 45% of would-be RTOs and loses 8% of genuine buyers; these are not yet measured. *Notice:* weekly reply rates, and RTO of confirmed vs unconfirmed orders. Even at 25% caught / 15% lost we still make ≈ ₹8,700 a month; if it's worse, raise the confirm threshold in the Rules screen (no code).
- **Fewer people take the ₹40 offer** (assumed 15%). *Notice:* daily prepaid conversion. At 5% we still make ≈ ₹11,400 a month; test ₹30 vs ₹50.
- **The model drifts** with a new campaign or new regions. *Notice:* a monthly check of predicted vs actual RTO for each lane, with an alert if it's off by 5+ points; retraining is one command.
- **Customers find the messages annoying.** *Notice:* opt-outs and "STOP" replies. There are already no messages from 9pm to 8am, one reminder only, and none at all for nudges.
- **AI misreads an address.** *Notice:* ops checks a weekly sample of 20. Its judgement only shifts the score; it can't cancel an order on its own.
- **Small sample.** September has 329 orders, so the ₹ figures could be off by a few thousand. The dashboard shows estimated vs actual ₹ every day: treat month one as a measured pilot.

## 5. With two more weeks

1. **Measure the assumptions:** run live, and let half of the confirm-lane orders skip confirmation for two weeks to get the real catch and drop-off rates.
2. **Move the prepaid offer to checkout** for high-risk districts and ₹1,500+ baskets, where it converts better and costs nothing to send.
3. **Fix address capture at checkout** (pincode → locality autofill, required house number). Junk addresses are our strongest risk signal, and they're preventable.
4. **Pull courier non-delivery reasons** (refused vs address not found vs unreachable); each needs a different fix.
5. **Test a ₹99 COD advance** for the 50%+ lane as a softer alternative to confirmation.
