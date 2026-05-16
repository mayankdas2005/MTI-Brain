# LPP Conversational Analytics

Reference architecture for answering the Treasury & Payments prompts
(the 4-step CFO demo and the Prompts_Retail_v_0.1 catalog) using
LangGraph orchestration over (a) the LPP Knowledge Graph in Jena and (b)
the Tribal / Brain graph of institutional memory.

<div class="kpis">

<div class="kpi">

Personas **8** · Analyst / Manager / Director / Executive × Finance /
Payments

</div>

<div class="kpi">

Prompts in scope **~150**

</div>

<div class="kpi">

Complexity tiers **Simple · Complex · Advanced**

</div>

<div class="kpi">

Graphs **2** · KG + Tribal

</div>

</div>

<div class="section">

## 1 · Design goal

Take any of the ~150 prompts in the Retail Treasury / Payments catalog,
route it through a LangGraph state machine, and return a grounded answer
that combines:

1.  **Formal facts** from the <span class="tag kg">KG</span> — the LPP
    ontology in Apache Jena Fuseki. Today: 14 banks, 24 companies, 115
    accounts, 9,547 investment positions, 1,800 FX forwards, 36,569
    derivative MTMs.
2.  **Institutional context** from the
    <span class="tag brain">Tribal</span> graph — board limits, policy
    thresholds, prior decisions, watchlists, commitments, hedge intent,
    SME-taught edges, recent incidents.
3.  **Persona & intent shaping** — an Analyst gets the data table, a CFO
    gets a one-pager with risks and a recommendation. Same backing
    query, different framing.

<div class="callout">

**Why both graphs.** The 4-step demo run showed the gap: Step 1–3
answered from KG alone; Step 4 ("does this require action?") cannot be
answered from KG facts because the answer depends on policy limits,
prior decisions, and current commitments. That is the tribal graph's
job.

</div>

</div>

<div class="section">

## 2 · The two-graph data plane

``` diagram
                  ┌─────────────────────────────────────┐
                  │      USER QUESTION + PERSONA        │
                  │  (Analyst / Manager / Director /    │
                  │   Executive  ×  Finance / Payments) │
                  └─────────────────────────────────────┘
                                   │
                                   ▼
                   ┌────────────────────────────────┐
                   │     LangGraph orchestration    │
                   │   (state machine, §4 below)    │
                   └────────────────────────────────┘
                       │                       │
              SPARQL  │                       │  SPARQL + GraphRAG
                       ▼                       ▼
   ┌───────────────────────────────┐  ┌────────────────────────────────┐
   │   KG  ─  Apache Jena Fuseki   │  │  TRIBAL / BRAIN  ─  Jena +     │
   │   /lpp dataset                │  │  Snowflake Cortex Search       │
   │                               │  │                                │
   │   Ontology: lpp:              │  │   Ontology: brain:             │
   │   • Bank, Company, Account    │  │   • Policy, Limit              │
   │   • InvestmentPosition        │  │   • Decision, Commitment       │
   │   • FxForward, DerivativeMtm  │  │   • Watchlist, Incident        │
   │   • Currency, InstrumentType  │  │   • SME-taught edges,          │
   │                               │  │     verbal commitments,        │
   │   416,818 triples             │  │     side letters, RM signals   │
   │   System of record:           │  │                                │
   │   Snowflake (R2RML)           │  │   System of record:            │
   │                               │  │   SME interviews + decision    │
   │                               │  │   minutes (Cortex Search       │
   │                               │  │   over transcripts)            │
   └───────────────────────────────┘  └────────────────────────────────┘
              ▲                                       ▲
              │  R2RML materialization                │  Tribal-fact ingest
              │                                       │  (SME UI + transcript NLP)
              │                                       │
   ┌────────────────────────────────────────────────────────────────────┐
   │             Snowflake  ─  raw + curated + conformed marts          │
   └────────────────────────────────────────────────────────────────────┘
```

Both stores expose the same surface to the agents: **SPARQL endpoints**.
The tribal graph adds a second retrieval path (Cortex Search over SME
transcripts) used only by the Brain Retrieval node.

</div>

<div class="section">

## 3 · LangGraph state machine

One deterministic graph. Conditional edges select the path based on
prompt complexity, persona, and whether the tribal graph is in play.
State carries:
`question, persona, intent, plan, ontology_terms, sparql, results, tribal_facts, evidence, answer, viz`.

``` diagram
                     ┌──────────────────────┐
                     │  intake_classify     │  persona, intent, complexity
                     └──────────┬───────────┘
                                ▼
                     ┌──────────────────────┐
              ┌──────│  router              │  picks specialist domain (F/P)
              │      │                      │  decides if tribal_needed
              │      └──────────┬───────────┘
              │                 ▼
              │      ┌──────────────────────┐
              │      │  domain_specialist   │  builds question plan
              │      │  (treasury / payments)│  — entities, time windows,
              │      │                      │    metrics, output shape
              │      └──────────┬───────────┘
              │                 ▼
              │      ┌──────────────────────┐
              │      │  ontology_lookup     │  resolves business terms →
              │      │                      │  KG classes / predicates
              │      └──────────┬───────────┘
              │                 │
              │   (tribal_needed)│      (not needed)
              │            ┌────┴────┐
              │            ▼         │
              │  ┌──────────────────┐│
              │  │ brain_retrieval  ││  GraphRAG over tribal graph:
              │  │ (GraphRAG)       ││  seed nodes + expansion depth
              │  └────────┬─────────┘│
              │           ▼          │
              │  ┌──────────────────────────┐
              │  │  sparql_gen              │  NL → SPARQL,
              │  │                          │  grounded in ontology + brain
              │  └────────┬─────────────────┘
              │           ▼
              │  ┌──────────────────────────┐
              │  │  sparql_validate         │  parse, SHACL, prefix check,
              │  │                          │  cardinality sanity
              │  └────────┬─────────────────┘
              │           │  fail  ─→ retry/repair (max 2)  ──┐
              │           ▼                                   │
              │  ┌──────────────────────────┐                 │
              │  │  governance_gate         │  row-level ACL, PII redact,
              │  │                          │  rate-limit, cost budget
              │  └────────┬─────────────────┘                 │
              │           ▼                                   │
              │  ┌──────────────────────────┐                 │
              │  │  sparql_execute          │  Fuseki /lpp +  │
              │  │                          │  /tribal        │
              │  └────────┬─────────────────┘                 │
              │           ▼                                   │
              │  ┌──────────────────────────┐                 │
              │  │  graph_reasoning         │  OWL inference, │
              │  │                          │  multi-hop joins│
              │  └────────┬─────────────────┘                 │
              │           ▼                                   │
              │  ┌──────────────────────────┐                 │
              │  │  verifier                │  cardinality,   │
              │  │                          │  unit checks,   │
              │  │                          │  threshold xref │
              │  └────────┬─────────────────┘                 │
              │           ▼                                   │
              │  ┌──────────────────────────┐                 │
              │  │  answer_synthesis        │  persona-shaped │
              │  │                          │  narrative with │
              │  │                          │  citations      │
              │  └────────┬─────────────────┘                 │
              │           ▼                                   │
              │  ┌──────────────────────────┐                 │
              │  │  visualization           │  table / chart /│
              │  │                          │  one-pager HTML │
              │  └────────┬─────────────────┘                 │
              │           ▼                                   │
              │     ┌──────────────┐                          │
              └────►│  human_in_loop│  (Advanced + Executive) │
                    │   (optional) │                          │
                    └──────┬───────┘                          │
                           ▼                                  │
                       ANSWER  ───────────────────────────────┘
                                          (retry edge)
```

Edges are deterministic; retries are bounded; every transition emits an
OpenTelemetry span. The end-state always contains **(answer, sparql,
results_uri, tribal_evidence, persona_render)** for audit.

</div>

<div class="section">

## 4 · Node responsibilities

| \# | Node | Input | Output | Backed by |
|----|----|----|----|----|
| 1 | **intake_classify** | Raw NL question + user profile | Persona, complexity (Simple / Complex / Advanced), intent class | LLM classifier |
| 2 | **router** | Classified intake | Domain (Finance / Payments), `tribal_needed` flag, output shape | Rules + small LLM |
| 3 | **domain_specialist** | Question + classification | Question plan: entities, time window, metrics, expected schema | Domain LLM with ontology preamble |
| 4 | **ontology_lookup** | Business terms | Resolved `lpp:` classes/predicates + skos labels | SKOS lookup on /lpp |
| 5 | **brain_retrieval** | Question plan | Seed `brain:TribalFact` URIs + Cortex Search hits + expansion depth | Cortex Search + tribal SPARQL |
| 6 | **sparql_gen** | Plan + ontology terms + tribal seeds | Draft SPARQL (UNION across KG + tribal named graphs) | LLM with strict grounding |
| 7 | **sparql_validate** | Draft SPARQL | Parse OK, SHACL OK, predicate-existence OK, cardinality sane | Jena ARQ parser + SHACL |
| 8 | **governance_gate** | Validated SPARQL + user | Filter rewrites (row-level ACL), PII mask, cost ceiling | Policy engine |
| 9 | **sparql_execute** | Final SPARQL | Result bindings (JSON), exec time, byte budget | Fuseki /lpp + /tribal |
| 10 | **graph_reasoning** | Bindings | Derived facts (subclass, transitive ownership, concentration math) | OWL inference or Python post-proc |
| 11 | **verifier** | Bindings + derived | Sanity flags: zero-rows, currency mismatches, threshold trips | Deterministic Python checks |
| 12 | **answer_synthesis** | Verified results + tribal facts + persona | NL answer with explicit citations to triples and SME sessions | LLM with grounded-only prompt |
| 13 | **visualization** | Verified results + persona | Table, chart, or one-page HTML (same engine that produced the demo report) | Templated renderer |
| 14 | **human_in_loop** | Draft answer (Advanced + Executive only) | Approval / edit / reject (AgentCore) | AgentCore HIL |

<span class="tag kg">KG</span> nodes touch Jena.
<span class="tag brain">Tribal</span> nodes touch Cortex Search + the
brain named graph. <span class="tag gold">LLM</span> nodes are stateless
and grounded; only nodes 1, 2, 3, 6, 12 invoke an LLM at all. The rest
are deterministic.

</div>

<div class="section">

## 5 · Routing by persona and complexity

The Prompts_Retail catalog spans 8 personas × 3 complexity tiers.
Routing decisions:

| Persona | Typical prompt | Path | Tribal needed? | HIL? |
|----|----|----|----|----|
| Analyst — F · Simple | "Show me the closing balance of our JPMorgan operating account for the last 7 days." | KG → table | No | No |
| Analyst — F · Complex | "Match yesterday's bank balances against our ERP ledger balances and list all discrepancies above \$10K" | KG (multi-source UNION) → reasoning → flagged table | No | No |
| Analyst — F · Advanced | "Detect vendors with anomalous payment patterns… flag \> 3σ from typical." | KG → reasoning (z-score) → tribal (vendor watchlist) → ranked flags | Yes — vendor watchlist, prior fraud rulings | No |
| Manager — F · Complex | "Show me our investment portfolio mix… flag positions outside investment policy guidelines." | KG → **tribal (policy thresholds)** → policy-overlaid table | **Yes** — policy lives in tribal | No |
| Director — F · Advanced | "Build a capital structure optimization model…" | KG (current debt/cash) → tribal (rating-agency targets, prior decisions) → scenario model → one-pager | Yes | **Yes** |
| Executive — F · Complex | "Give me a one-page CFO briefing on treasury health." | KG (Q1+Q3a+Q3b+Q3c bundle) → tribal (risk register, prior briefings) → one-pager | Yes | Yes |
| Executive — F · Advanced | "Model the enterprise impact of a major black swan scenario…" | Multi-cycle: KG snapshot → tribal commitments → scenario engine → HIL | Yes | Yes |
| Analyst — P · Simple | "Show me total card payment volume processed… yesterday." | KG → table | No | No |
| Analyst — P · Complex | "Reconcile gross card sales against net settlements… break out variance by interchange, fees, chargebacks." | KG (multi-feed reconcile) → reasoning → variance table | Optional — for fee schedule | No |
| Director — P · Advanced | "Build a 5-year payments strategy roadmap…" | KG snapshot → tribal (commitments, contracts, member growth plan) → HIL → deck | Yes | Yes |

<div class="callout brain">

**Heuristics for `tribal_needed`.** Set the flag whenever the prompt
contains: *policy, limit, threshold, watchlist, recommend, should we,
breach, anomaly vs. typical, committee, board, prior decision,
commitment, intent, hedge purpose, strategy, roadmap, 5-year, optimize,
black swan, scenario*. These are signals that the answer requires
institutional context, not just instrument facts.

</div>

</div>

<div class="section">

## 6 · Three worked traces from the catalog

### 6a · <span class="tag green">SIMPLE</span> Analyst-F \#1: "What is our total cash balance across all bank accounts as of yesterday?"

<div class="lane kg">

<div class="label">

Path

</div>

<div>

intake_classify (Analyst, Simple, "balance lookup") → router (Finance,
tribal_needed=false) → domain_specialist (entities: BankAccount; time:
yesterday; metric: bookValue SUM) → ontology_lookup (`lpp:BankAccount`,
`lpp:bookValue`, `lpp:asOfDate`) → sparql_gen (UNION-free aggregation) →
validate → gate → execute → verify (one row, currency uniform) →
synthesis ("\$X across N accounts, vs. \$Y 7-day average") → viz
(table + sparkline)

</div>

</div>

### 6b · <span class="tag gold">COMPLEX</span> Manager-F \#11: "Show me investment portfolio mix… flag positions outside investment policy guidelines."

<div class="lane both">

<div class="label">

Path

</div>

<div>

intake_classify (Manager, Complex, "policy compliance") → router
(Finance, **tribal_needed=true**) → domain_specialist → ontology_lookup
→ **brain_retrieval** — pulls active `brain:Policy` facts: max
single-issuer 10%, min A-rating, max duration 365d → sparql_gen — one
query joining `lpp:InvestmentPosition` to `brain:Limit` via
`brain:appliesTo` → validate → gate → execute → graph_reasoning (compute
% of book, compare to limit) → verifier (flag positions where
limit_pct\<observed_pct) → synthesis ("3 breaches: position X at 14% vs
10% issuer cap, sourced from Policy 2024-IM-07, decided 2024-09-15 by
Treasury Committee") → viz (policy-overlaid table)

</div>

</div>

### 6c · <span class="tag red">ADVANCED</span> Executive-F \#11: "Give me a one-page CFO briefing on treasury health…"

<div class="lane both">

<div class="label">

Path

</div>

<div>

intake_classify (Executive, Complex, "executive briefing") → router
(Finance, tribal_needed=true, output_shape=**one_pager**) →
domain_specialist **splits into sub-plans**: {liquidity, debt, FX, IR,
risks} → For each: ontology_lookup → sparql_gen → execute (parallel
fan-out) → brain_retrieval pulls `brain:RiskRegister`, recent
`brain:Decision` minutes, `brain:Commitment` in next 30d →
graph_reasoning (concentration %, threshold checks against tribal
limits) → verifier (snapshot-staleness check, currency reconcile) →
synthesis (CFO-grade one-pager with risks + recommendations) → viz (HTML
one-pager — *this is the cfo-demo-report.html artifact*) →
**human_in_loop** (Treasury Director reviews before CFO send)

</div>

</div>

Trace 6c is the demo we just ran. KG produced Steps 1–3; the Brain layer
is what lets Step 4 ("does this need action?") become a recommendation
rather than a "data not available" refusal.

</div>

<div class="section">

## 7 · State shape and node code stubs

``` code
from typing import TypedDict, Literal, Annotated
from langgraph.graph import StateGraph, END

class S(TypedDict):
    question: str
    user: dict                # { id, role, region, entitlements }
    persona: Literal["analyst-F","manager-F","director-F","executive-F",
                     "analyst-P","manager-P","director-P","executive-P"]
    complexity: Literal["simple","complex","advanced"]
    intent: str
    domain: Literal["finance","payments"]
    tribal_needed: bool
    plan: dict                # entities, time, metrics, output_shape
    ontology_terms: list[dict]
    tribal_facts: list[dict]  # seed brain: nodes + scores
    sparql: str
    validation: dict
    bindings: list[dict]
    derived: dict
    verification: dict
    answer: str
    evidence: list[dict]      # triples + SME session refs
    viz: dict                 # html | table | chart

def router(s: S) -> S:
    s["domain"]    = pick_domain(s["question"], s["persona"])
    s["tribal_needed"] = needs_tribal(s["question"], s["intent"])
    return s

def brain_retrieval(s: S) -> S:
    seeds = cortex_search("TRIBAL_FACT_SEARCH",
                          query=s["question"],
                          today=now(),
                          persona=s["persona"])
    expanded = sparql_traverse("/tribal",
                               seed_uris=[h.uri for h in seeds],
                               depth=2,
                               predicates=["brain:appliesTo","brain:supersedes","brain:committed"])
    s["tribal_facts"] = seeds + expanded
    return s

def sparql_gen(s: S) -> S:
    s["sparql"] = llm_generate(
        system=GROUNDED_SPARQL_PROMPT,
        ontology=s["ontology_terms"],
        tribal_seeds=s["tribal_facts"],
        plan=s["plan"])
    return s

# ---- graph wiring -------------------------------------------------
g = StateGraph(S)
for n in [intake_classify, router, domain_specialist, ontology_lookup,
          brain_retrieval, sparql_gen, sparql_validate, governance_gate,
          sparql_execute, graph_reasoning, verifier, answer_synthesis,
          visualization, human_in_loop]:
    g.add_node(n.__name__, n)

g.set_entry_point("intake_classify")
g.add_edge("intake_classify", "router")
g.add_conditional_edges("router",
    lambda s: "brain_retrieval" if s["tribal_needed"] else "sparql_gen")
g.add_edge("brain_retrieval", "sparql_gen")
g.add_edge("sparql_gen", "sparql_validate")
g.add_conditional_edges("sparql_validate",
    lambda s: "sparql_gen" if not s["validation"]["ok"] and s["validation"]["retries"]<2
              else "governance_gate")
g.add_edge("governance_gate", "sparql_execute")
g.add_edge("sparql_execute", "graph_reasoning")
g.add_edge("graph_reasoning", "verifier")
g.add_edge("verifier", "answer_synthesis")
g.add_edge("answer_synthesis", "visualization")
g.add_conditional_edges("visualization",
    lambda s: "human_in_loop"
              if s["complexity"]=="advanced" and s["persona"].startswith(("director","executive"))
              else END)
g.add_edge("human_in_loop", END)

app = g.compile(checkpointer=checkpointer_postgres)
```

</div>

<div class="section">

## 8 · Tribal graph — what lives there

| Brain class | Example facts (from the catalog prompts) | Source of truth |
|----|----|----|
| `brain:Policy` | Counterparty ≤ 25% of total exposure; min investment rating A; FX hedge ratio ≥ 60% of forecasted EUR; debt covenant Net Debt/EBITDA \< 3.0× | Treasury Policy doc, board minutes |
| `brain:Limit` | \$200M minimum operating cash; \$5B revolver capacity; \$1B CP program ceiling; single-vendor 25% concentration cap | Approved limit register |
| `brain:Decision` | "Reduce Citi to 20% by Q4 2026" — Treasury Committee 2026-03-12 | Committee minutes |
| `brain:Commitment` | \$230M KRW settlement on 2026-05-18; \$750M dividend payment 2026-06-30; \$400M debt maturity 2026-08-15 | Forward calendar |
| `brain:Watchlist` | Citibank — elevated CDS spreads as of 2026-04-30; Vendor X — OFAC review pending | Credit/compliance team |
| `brain:HedgeIntent` | KRW notional categorized as translation hedge for Korean subsidiary (not transaction) | Hedge documentation |
| `brain:Incident` | 2026-04-22 ACH file rejection batch, root cause vendor master mismatch | Ops postmortem |
| `brain:SMEEdge` | "BANK_HSBC London branch is the primary KRW liquidity provider, not Singapore" (taught by SME Maria Chen, 2026-03-04) | SME interview transcript |

Every tribal fact carries `brain:effectiveFrom` / `brain:effectiveTo`,
`brain:status`, `brain:source`, and `brain:supersedes`. Old facts are
not deleted — they are superseded, preserving the decision audit trail.

</div>

<div class="section">

## 9 · Personas shape the answer, not the data

<div class="persona-grid">

<div class="persona">

**Analyst**Wide tabular detail, source columns, raw counts. SPARQL
surfaced. Few prose lines.

</div>

<div class="persona">

**Manager**Aggregated KPIs vs. policy. Drill-down link to analyst view.
Variance call-outs.

</div>

<div class="persona">

**Director**Risk register + trend + benchmark. Tribal facts inline as
"context".

</div>

<div class="persona">

**Executive**One-page narrative, top 3 risks, recommendation. SPARQL
hidden. HIL gate.

</div>

</div>

The **same backing SPARQL** can serve all four; `answer_synthesis` and
`visualization` pick the render based on persona. The Analyst's table
and the Executive's one-pager come out of the same node graph — only the
final two nodes differ in temperature and template.

</div>

<div class="section">

## 10 · Coverage map — what answers cleanly, what doesn't

| Catalog tier | Count | KG alone covers | Needs tribal | Out of scope today |
|----|----|----|----|----|
| Simple | ~50 | ~45 (90%) | ~5 | 0 |
| Complex | ~60 | ~30 (50%) | ~25 | ~5 (peer-benchmark, macro) |
| Advanced | ~40 | ~5 | ~25 | ~10 (ESG, pension OCI, geopolitical projections, peer M&A) |

<div class="callout warn">

**What is genuinely out of scope.** Some Director/Executive prompts
("impact of the recent gulf war", "compare leverage vs
Walmart/Target/Home Depot", "model EPS impact of 100bps rate hike on
pension discount rate") require *external* data the LPP graph does not
own — macro feeds, peer financial filings, pension actuarial models. The
platform should **refuse honestly** on these rather than hallucinate,
and direct the user to the relevant external system. This refusal itself
is a tribal-graph fact (`brain:OutOfScopeReason`).

</div>

</div>

<div class="section">

## 11 · Non-functional cuts

| Concern | How it's handled |
|----|----|
| Latency budget | Simple ≤ 3s, Complex ≤ 10s, Advanced ≤ 45s (with HIL out of band). Parallel fan-out in domain_specialist for multi-section briefings. |
| Cost budget | governance_gate enforces per-query LLM token + Fuseki row caps; sparql_validate rejects queries with cartesian risk. |
| Determinism | Only nodes 1, 2, 3, 6, 12 use an LLM. Same SPARQL output produces same answer. Checkpoints in Postgres for replay. |
| Audit | Every answer ships with (SPARQL, result-hash, tribal_evidence_uris, prompt_template, model_version). AgentCore stores the trace. |
| Entitlements | governance_gate rewrites SPARQL with `FILTER`s based on user's region/entity access. Single user can never see another's row. |
| Freshness | KG materialized from Snowflake on a CDC schedule per domain (treasury 15-min, payments 5-min). Tribal facts have explicit effective dates and are queried with `FILTER(now() BETWEEN effectiveFrom && effectiveTo)`. |
| Hallucination control | sparql_gen prompt allows only ontology terms supplied by ontology_lookup. sparql_validate rejects unknown predicates. answer_synthesis prompt forbids facts not in `bindings` or `tribal_facts`. |

</div>

<div class="section">

## 12 · What changes vs. the existing architecture doc

The 1,385-line `sparql-conversational-analytics-architecture.md` already
specifies the layered model, 12-agent decomposition, and Brain-layer
ontology. This document is a **focused operational view** of that
design, specialized to:

1.  The actual 4-step CFO demo run on the LPP dataset (Step 4 ↔ Brain
    motivation).
2.  The Prompts_Retail catalog as the input distribution — mapping each
    tier to a path.
3.  A concrete LangGraph `StateGraph` wiring with conditional edges for
    tribal vs. non-tribal paths and HIL gating for Executive/Advanced
    prompts.
4.  An explicit coverage map for what fails honestly vs. what answers
    cleanly today.

</div>

Generated by Claude Code. Companion to
[cfo-demo-report.html](cfo-demo-report.html) and
[cfo-demo-qa.html](cfo-demo-qa.html). Source artifacts:
`../semantic/lpp-ontology.ttl`, `../fuseki-data/lpp-data.ttl`,
`../sparql-conversational-analytics-architecture.md`,
`../Prompts_Retail_v_0.1.xlsx`.
