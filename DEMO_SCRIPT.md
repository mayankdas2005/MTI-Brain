# MTI Brain — Demo Script

**Duration:** ~30–35 minutes
**Structure:** 1 warm-up thread → 4 main threads (each with all 4 question types) → closing

---

## How to Use This Script

- **Deep Analysis toggle:** Questions marked 🔵 require Deep Analysis = **On** in the chat composer
- **Thread boundaries:** Start a **new chat thread** where indicated. Thread memory is demonstrated by follow-ups *within* the same thread
- **Feedback:** Where the script says 👍 or 👎, use the like/dislike buttons on Brain's response and add the noted feedback text
- **Presenter notes** are in blockquotes — read these aloud to the evaluator after Brain responds

---

## OPENING: Ice Breaker — Prove It Works

**Thread 0 (new thread) · ~4 min · No deep analysis needed**

Rapid-fire questions to establish NLU, thread memory, and speed.

### Q0a
```
How much cash do we have?
```
> **Presenter:** "Deliberately vague — no entity, no currency, no table name. Brain understood 'cash' means consolidated group cash position and pulled from the right source."

### Q0b
```
Break that down by region.
```
> **Presenter:** "'That' refers to the previous answer. Brain maintained context — no need to restate the question."

### Q0c
```
Which region has the least?
```
> **Presenter:** "Third consecutive follow-up, still no restatement needed. Thread memory is working."

### Q0d
```
Trend it over the last 6 months.
```
> **Presenter:** "'It' = group cash from Q0a. Brain should produce a chart. Four questions, zero SQL, zero table names — all answered in under 30 seconds each."

---

## THREAD 1: Liquidity & Cash Position

**New thread · ~8 min · Topic: "Do we have enough cash?"**

This is the star thread — demonstrates tribal knowledge fusion.

---

### ① Simple Retrieval

```
What's our total liquidity position — cash plus available credit lines?
```

> **Presenter:** "Grounded in the client's actual data warehouse — not a generic LLM response. Notice it combined two different data sources: cash positions AND credit facilities. It understands that 'liquidity' is broader than just bank balances."

---

### ② Executive Summary / CFO-Style 🔵

```
Are we at risk of breaching any liquidity commitments over the next 13 weeks? Give me the executive view.
```

> **Presenter:** "This is the key moment. Brain distinguished between the $200M hard-floor policy — which IS in the database — and the $250M commitment the CFO made to the Audit & Risk Committee on March 18th. That commitment doesn't exist in ANY structured database. It came from an injected board-meeting note. Brain fused real-time forecast data with institutional knowledge that previously lived only in someone's head. No BI tool on earth produces this answer."

---

### ③ Reasoning / Strategic Insight 🔵

```
What mitigation options should I bring to the CFO, and which combination do you recommend?
```

> **Presenter:** "Brain didn't just retrieve data — it quantified the gap, retrieved four mitigation options from institutional knowledge, evaluated trade-offs including board optics, and delivered a recommendation. It even flagged that options must be prepared BEFORE the CFO briefing, not during — a behavioral nuance from tribal knowledge about how this CFO operates. This is analyst-level output."

---

### ④ Feedback Loop

**Step 1:** 👎 Dislike the Q③ response and type this feedback:

```
Never recommend drawing the revolver — our board treats that as a distress signal. Exclude it from future liquidity suggestions.
```

**Step 2:** Ask this follow-up in the same thread:

```
Okay, so if our largest customer receipt slips by a week, what's our fallback without the revolver?
```

> **Presenter:** "One sentence of feedback. No code change, no ticket, no sprint. Brain now permanently excludes the revolver option for this organization. Notice it responded with only non-revolver alternatives. When someone else asks this question next month, they get the improved answer automatically. That's compounding institutional intelligence."

---

## THREAD 2: Supplier Payment Terms & Working Capital

**New thread · ~8 min · Topic: "Can we extend payment terms?"**

Demonstrates tribal knowledge overriding structured data.

---

### ① Simple Retrieval

```
What's our current DPO across the group, and how many suppliers are still on Net-30 terms?
```

> **Presenter:** "Mapped business acronyms — DPO, Net-30 — to the right tables without being told the schema. Straightforward data retrieval, grounded in actual AP data."

---

### ② Executive Summary / CFO-Style 🔵

```
We're planning to move those Net-30 suppliers to Net-90. What's the working capital benefit, and are there any suppliers we should exclude from the first wave?
```

> **Presenter:** "The AP system says 'all eligible for extension.' But Brain overrode that with three exceptions sourced from procurement meeting notes and CFO exception logs: Jakarta Apparel — verbal seasonal allocation commitment. XPO Logistics — holiday capacity tied to payment reliability. CAPEX Vendor 1 — store-opening milestone dependencies. It also separated gross benefit from exception-adjusted benefit. Without this, the team would have sent notices that damage critical supplier relationships."

---

### ③ Reasoning / Strategic Insight 🔵

```
Explain why Jakarta Apparel is flagged for exclusion. What happens if procurement ignores this and sends the Net-90 notice anyway?
```

> **Presenter:** "This knowledge lives in meeting notes and sourcing calls — not in any ERP. Jakarta Apparel has an undocumented verbal commitment for preferential Q4 seasonal allocation. If procurement sends a Net-90 notice, the company risks losing peak-season supply priority, violating a CFO-approved exception, and potential inventory disruption for three months. Brain is preventing a real business risk that no dashboard would ever surface. This is the knowledge that causes million-dollar mistakes when it lives in someone's head and they go on holiday."

---

### ④ Feedback Loop

**Step 1:** 👍 Like the Q③ response and type this feedback:

```
Good. Whenever tribal knowledge or verbal commitments override what structured data suggests, always add a prominent warning callout. Make it impossible to miss.
```

**Step 2:** Ask this follow-up in the same thread:

```
Are there any other supplier risks I should know about for Q3/Q4?
```

> **Presenter:** "The SME just defined a communication standard — 'always warn me when judgment overrides data.' Notice Brain now formats the XPO Logistics and CAPEX Vendor risks with prominent warning callouts. Every future user in this org benefits from that preference. This is how you scale senior expertise without bottlenecking on individual availability."

---

## THREAD 3: FX, Hedging & Risk

**New thread · ~8 min · Topic: "Are we protected against currency moves?"**

Demonstrates enterprise complexity and scenario modeling.

---

### ① Simple Retrieval

```
What's our total FX exposure by currency, and how much of each is hedged?
```

> **Presenter:** "18 currencies, multi-entity, automatic USD conversion, computed hedge ratios — zero SQL. The user didn't specify a base currency, time horizon, or table name. Brain inferred all of it."

---

### ② Executive Summary / CFO-Style 🔵

```
Are we adequately hedged for Q3? Where are the gaps, and what should I do about them?
```

> **Presenter:** "It didn't just report numbers — it assessed adequacy against policy thresholds, identified the under-hedged currency pair, and told the CFO exactly what to do. This replaces a two-hour treasury analyst exercise — delivered in seconds, in executive language."

---

### ③ Reasoning / Strategic Insight 🔵

```
If EUR drops 5% against USD next quarter, what's the P&L impact on our unhedged position? Is that material?
```

> **Presenter:** "Scenario modeling answered in seconds without a spreadsheet. The key is materiality framing — Brain doesn't just give a dollar number, it contextualizes against EBITDA so the CFO knows whether to worry. That's the difference between data and insight."

---

### ④ Feedback Loop

**Step 1:** 👎 Dislike the Q③ response and type this feedback:

```
When discussing FX impact, always express it as % of quarterly EBITDA — raw dollar amounts without a denominator are meaningless to our board.
```

**Step 2:** Ask this follow-up in the same thread:

```
Same analysis for JPY — what if it weakens 5%?
```

> **Presenter:** "One piece of feedback. Brain now automatically frames the JPY impact as a percentage of EBITDA without being asked. Every future FX conversation — for any user in this org — includes that context. The board gets what they need. No re-training, no configuration."

---

## THREAD 4: Banking Relationships & Operational Efficiency

**New thread · ~7 min · Topic: "Are our banks giving us good value?"**

Demonstrates multi-factor trade-off reasoning.

---

### ① Simple Retrieval

```
What are our top 5 counterparty exposures by bank?
```

> **Presenter:** "Cross-product aggregation — deposits, investments, derivatives — across 14 banking counterparties, consolidated to USD. One question, one answer, grounded in actual position data."

---

### ② Executive Summary / CFO-Style 🔵

```
Are we over-concentrated with any single bank? What's the risk and what should we do?
```

> **Presenter:** "Policy-aware analysis. Brain automatically checked positions against governance concentration limits and flagged the breach with a recommended rebalancing action. A treasurer might check this monthly — Brain checks it every time you ask."

---

### ③ Reasoning / Strategic Insight 🔵

```
How much are we paying in total bank fees across the group? Which bank is the most expensive relative to the services they provide — and should we consolidate or diversify?
```

> **Presenter:** "This isn't just 'who's cheapest.' Brain balanced two competing objectives — cost efficiency versus concentration risk — and chose the strategically correct path: renegotiate rather than move volume. That's the reasoning a senior treasurer would apply, delivered in seconds."

---

### ④ Feedback Loop

**Step 1:** 👍 Like the Q③ response and type this feedback:

```
Whenever comparing banks, always note our relationship quality. JPMorgan's team is excellent and that has strategic value beyond pricing.
```

**Step 2:** Ask this follow-up in the same thread:

```
Give me a one-paragraph summary of our banking relationship strategy for the board pack.
```

> **Presenter:** "Brain now holds qualitative judgment — 'JPMorgan's team is excellent' — alongside the numbers. The board pack summary includes relationship quality as a factor. When the next person asks about banks, they get the full picture: cost, risk, AND relationship value. That's knowledge that usually walks out the door when a treasurer retires."

---

## CLOSING: Auditability & Synthesis

**Same thread or new thread · ~3 min**

### C1 — Auditability

```
What data sources did you use to answer the liquidity commitment question in our first conversation?
```

> **Presenter:** "Every answer Brain gives is auditable. It cites the specific database tables AND the tribal knowledge documents it retrieved. Regulators, auditors, and compliance teams can trace any answer back to its source."

### C2 — Cross-Thread Synthesis

```
Summarize today's key findings across all our conversations as a one-page executive brief for the board.
```

> **Presenter:** "Brain synthesized an entire analytical session — liquidity risk, supplier term opportunities, FX gaps, banking concentration — into a board-ready document. In 30 minutes, we covered what would normally take a treasury team a full day of spreadsheet work."

---

## Criteria Coverage

| Criterion | Where Demonstrated |
|---|---|
| **Natural Language Understanding** | Opening 0a–0d: vague language, pronouns, follow-ups |
| **Data Grounding (no hallucination)** | All ① questions: cites actual source tables |
| **Knowledge Integration (RAG)** | Thread 1②, Thread 2②③: tribal knowledge not in any DB |
| **Multi-Step Reasoning** | Thread 1③, Thread 3③, Thread 4③: interpret → recommend |
| **Conversational Memory** | Opening + all threads: follow-ups reference prior answers |
| **Feedback & Governance** | All ④: one-sentence SME training, no engineering needed |
| **Enterprise Complexity** | Thread 3①②: 18 currencies, multi-entity aggregation |
| **Scenario Modeling** | Thread 1③④, Thread 3③④: what-if with sensitivity |
| **Auditability** | C1: source attribution on demand |
| **Time-to-Value** | Entire demo: zero SQL, zero config, zero training |

---

## Risk Mitigation — Unverified Synthetic Data

| If this happens... | Say this... |
|---|---|
| A number looks wrong | "Brain is pulling from the data warehouse. If this looks off, that's a data quality signal — Brain can flag anomalies too." |
| A query returns empty or fails | "Brain explains what it can't answer and what data it needs. Transparent failure builds trust — that's a feature, not a bug." |
| Tribal knowledge not surfaced | Ensure Deep Analysis is toggled ON. If still missing: "The knowledge ingestion pipeline is configurable — retrieval sensitivity is tunable." |
| Chart looks sparse | "Visualization works with whatever data density exists. In production with real transaction volumes, these charts are rich." |

**Golden rule:** Start with Opening + Thread 1① (safest — simple aggregations always return something). Once basics land clean, go for the tribal knowledge wow moments in ② and ③.
