# Plan · Execute · Reflect · Repair

Multi-step planning, looping, validation, and self-correction on top of
the kernel + pack design. Answers the question: can this architecture
handle "Build a 4-week and 3-month forecast factoring seasonality, flag
breaches" — not as a single SPARQL but as a coordinated multi-step plan
that validates and corrects itself.

<div class="section">

## 1 · Why the linear graph isn't enough

The 14-node LangGraph in [architecture.html](architecture.html) handles
a single-question / single-SPARQL flow. That covers ~90% of Simple
prompts. It does **not** cover the Advanced tier, which routinely needs:

| Prompt | Why one SPARQL isn't enough |
|----|----|
| "Build a 4-week and 3-month cash forecast factoring seasonality and flag weeks below \$200M." | Needs (a) maturity ladder, (b) same-period-last-year flows, (c) policy threshold, (d) breach detection. Different queries. Different graphs. |
| "Run a stress test on liquidity assuming a 20% drop in receipts for 30 days; show which entities breach first." | Baseline + scenario re-projection + per-entity threshold lookup + ranking. Each step depends on the previous result. |
| "Reconcile yesterday's gross card sales against net acquirer settlements; break variance into interchange, fees, chargebacks." | Pull POS sales, pull acquirer settlements, pull fee schedule from tribal, join, decompose. Four-step DAG. |
| "Detect vendors with payment patterns \>3σ from typical." | Baseline distribution per vendor (one query per vendor or one big query with windowing) → outlier flagging → tribal watchlist join. |

The kernel needs an **outer loop** that plans these into sub-questions,
sequences them, validates intermediate results, and repairs when
something is wrong.

</div>

<div class="section">

## 2 · The outer loop — Plan / Execute / Reflect

``` diagram
                            USER QUESTION + PERSONA
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │   complexity_gate       │  Simple? → straight to inner pipeline
                          │   (intake_classify out) │  Complex/Advanced? → enter outer loop
                          └────────────┬────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
        ┌─────────────────│   PLANNER               │  decompose into sub-question DAG
        │                 │   (LLM with planner     │  • each sub-Q is a single-SPARQL job
        │                 │    prompt + pack rules) │  • declare deps + parallel-safe edges
        │                 └────────────┬────────────┘
        │                              │
        │                              ▼
        │                 ┌─────────────────────────┐
        │                 │   PLAN_VALIDATOR        │  • all sub-Qs resolvable from pack?
        │                 │                         │  • DAG acyclic?
        │                 │                         │  • total budget within cap?
        │                 └────────────┬────────────┘
        │                              │  ok?
        │                              ▼
        │                 ┌─────────────────────────┐
        │                 │   EXECUTOR LOOP         │  iterate ready sub-Qs
        │     ┌──────────►│                         │
        │     │           │   for sub_q in ready:   │
        │     │           │     run inner_pipeline  │  ← the 14-node graph
        │     │           │     write result to     │     from architecture.html
        │     │           │       scratchpad        │     runs PER sub_q
        │     │           └────────────┬────────────┘
        │     │                        │
        │     │                        ▼
        │     │           ┌─────────────────────────┐
        │     │           │   STEP_REFLECTOR        │  per-step gates:
        │     │           │                         │  • parse ok?
        │     │           │                         │  • SHACL ok?
        │     │           │                         │  • cardinality sane?
        │     │           │                         │  • answers the sub-Q?
        │     │           └────┬─────────────┬──────┘
        │     │                │             │
        │     │           pass │             │ fail
        │     │                ▼             ▼
        │     │      ┌───────────────┐  ┌─────────────────────┐
        │     │      │ next sub_q?   │  │   REPAIRER          │  3 escalation levels:
        │     │      │ or done?      │  │   (Level 1→2→3)     │  L1: SPARQL repair (regen)
        │     │      └──┬───────┬────┘  └──────────┬──────────┘  L2: sub-Q replan
        │     │   more │       │ done             │              L3: full plan replan
        │     │        │       ▼                  │ retry?       on 3× L3 → halt+refuse
        │     │        │   ┌──────────────────────┴──────┐
        │     │        │   │      back to EXECUTOR LOOP  │
        │     │        └───┴──────────────────────┐      │
        │     └─────────────────────────────────────────┘
        │                                          │
        │                                          ▼
        │                             ┌─────────────────────────┐
        │                             │   FINAL_REFLECTOR       │  outcome gate:
        │                             │                         │  • all sub-Qs answered?
        │                             │                         │  • cross-step consistency?
        │                             │                         │  • answers original question?
        │                             └────────┬────────────────┘
        │                                      │
        │                                fail  │  pass
        │             ┌────────────────────────┤
        │             │ (replan budget left?)  ▼
        └─────────────┤              ┌─────────────────────────┐
                      │              │   answer_synthesis      │  combine scratchpad into
                      ▼              │   (persona-shaped)      │  persona-shaped narrative
                no budget?           └────────┬────────────────┘
                      ▼                       ▼
                ┌─────────────┐      ┌─────────────────────────┐
                │ refuse with │      │   visualization + HIL   │
                │ partial     │      └─────────────────────────┘
                │ findings    │
                └─────────────┘
```

This is a textbook Plan-and-Execute / ReAct hybrid. The kernel ships it
as five extra LangGraph nodes that wrap the existing 14-node inner
pipeline.

</div>

<div class="section">

## 3 · Five new nodes (kernel-level)

| Node | Job | Output | Backed by |
|----|----|----|----|
| <span class="tag plan">PLAN</span> planner | Decompose into sub-Q DAG. | `Plan{nodes: [SubQ], edges: [dep]}` | LLM with planner template from pack + ontology terms |
| <span class="tag plan">PLAN</span> plan_validator | Sanity-check plan before spending budget. | `{ok, errors, est_cost}` | Deterministic Python: DAG, budget, ontology coverage |
| <span class="tag act">ACT</span> executor | Iterate ready sub-Qs (topo + parallel). | Scratchpad of bindings + provenance | Recursive call into inner pipeline; async fan-out |
| <span class="tag reflect">REFLECT</span> step_reflector | Per-step pass/fail decision. | `{pass, reason, fix_hint, level}` | Rules + small LLM judge |
| <span class="tag fix">FIX</span> repairer | Apply L1/L2/L3 correction. | Updated plan or sub-Q | LLM with explicit repair prompt + last error |
| <span class="tag reflect">REFLECT</span> final_reflector | Does the assembled scratchpad answer the original question? | `{pass, missing, replan_hint}` | LLM judge with grounded-only prompt |

The inner pipeline (intake_classify → visualization) is unchanged. The
executor calls it per sub-Q. The reflectors and repairer are the new
feedback loop.

</div>

<div class="section">

## 4 · State shape for the loop

``` code
class Plan(TypedDict):
    nodes: list["SubQ"]
    edges: list[tuple[str, str]]    # dep_from, dep_to
    rationale: str                  # why this decomposition
    budget: BudgetEnvelope          # tokens, seconds, fuseki rows, $ cap

class SubQ(TypedDict):
    id: str
    question: str                   # NL form
    depends_on: list[str]           # other SubQ ids
    output_shape: dict              # expected schema
    status: Literal["pending","running","done","failed","skipped"]
    bindings: list[dict] | None
    sparql: str | None
    error: str | None
    attempt: int                    # incremented by repairer
    parent_attempts: int            # plan-level replan counter

class S(TypedDict):
    # ... all fields from the inner state ...
    plan: Plan
    scratchpad: dict[str, SubQ]     # keyed by SubQ.id
    plan_attempts: int              # global replan counter
    budget_used: BudgetUsage
    halt_reason: str | None
```

The state is checkpointed to Postgres after every node. A failed run can
be resumed mid-loop with full provenance.

</div>

<div class="section">

## 5 · Validation — what gets checked, where

| Layer | Check | When | Fail action |
|----|----|----|----|
| **plan_validator** | Plan DAG is acyclic and topologically sortable | After PLAN | Replan (L3) |
| **plan_validator** | Every sub-Q's ontology terms resolvable in the active pack | After PLAN | Replan (L3) |
| **plan_validator** | Estimated cost ≤ budget envelope | After PLAN | Trim plan or replan |
| **inner sparql_validate** | SPARQL parses | Per sub-Q | SPARQL repair (L1) |
| **inner sparql_validate** | SHACL shapes pass (predicate exists, datatype, cardinality declared) | Per sub-Q | SPARQL repair (L1) |
| **inner sparql_validate** | Allowed-predicate set respected (no hallucinated predicates) | Per sub-Q | SPARQL repair (L1) — hard fail after 2 attempts |
| **inner verifier** | Cardinality sane (no empty result on a question that expects rows; no million-row blow-up) | Per sub-Q | SPARQL repair (L1) or sub-Q replan (L2) |
| **inner verifier** | Units / currencies uniform; threshold cross-checks against tribal | Per sub-Q | SPARQL repair (L1) |
| **step_reflector** | "Does this result answer this sub-Q?" (LLM judge) | Per sub-Q after verifier | L1 or L2 escalation |
| **final_reflector** | "Does the assembled scratchpad answer the original question?" | After all sub-Qs done | L3 replan if budget left; else refuse |
| **governance_gate** | Row-level ACL, PII, cost cap | Per sub-Q before execute | Hard fail — cannot be repaired |
| **human_in_loop** | Executive/Advanced approval | After final_reflector pass | Edit or reject — reject loops back to repairer |

Every check produces a structured `ReflectionEvent` with
`{level, reason, fix_hint, evidence}` that feeds the repairer's prompt
and is persisted for audit.

</div>

<div class="section">

## 6 · Self-correction — three escalation levels

| Level | Trigger | What changes | What stays | Cap |
|----|----|----|----|----|
| <span class="tag fix">L1</span> SPARQL repair | Parse error, SHACL fail, empty result, cardinality blow-up, predicate not found | Regenerate the SPARQL for the same sub-Q with the error as a feedback hint | Plan, sub-Q wording, scratchpad of other sub-Qs | 2 attempts per sub-Q |
| <span class="tag fix">L2</span> Sub-Q replan | L1 exhausted; or step_reflector says "the SPARQL is correct but doesn't answer this sub-Q" | Rewrite the sub-Q (split, narrow, reshape) and regenerate SPARQL | Other sub-Qs unchanged | 1 attempt per sub-Q |
| <span class="tag fix">L3</span> Plan replan | L2 exhausted on a critical sub-Q; or final_reflector says the assembled answer is wrong shape | Re-decompose the whole question with the failure history as input | Question, persona, pack | 2 attempts per request |
| <span class="tag stop">HALT</span> Honest refusal | L3 exhausted, or budget exhausted, or governance hard-fail | Stop, return partial findings with explicit "what failed and why" | — | — |

<div class="callout warn">

**Circuit breakers are non-negotiable.** Without strict per-level caps,
an LLM in a repair loop can burn unlimited tokens. Every attempt
increments a counter that is checked *before* the next LLM call. Budget
exhaustion is an honest refusal, not silent infinite recursion.

</div>

Each level's repair prompt is structured: it gets *the last attempt*,
*the exact error*, *the relevant ontology slice*, and a directive to fix
only the named problem.

``` code
REPAIR_PROMPT_L1 = """You generated this SPARQL:
{last_sparql}

It failed with: {error}

Fix only this problem. Do not change predicates that worked.
Allowed predicates: {ontology_slice}
Allowed datasets: {datasets}

Return ONLY the corrected SPARQL.
"""
```

</div>

<div class="section">

## 7 · Worked trace — Advanced prompt

**Prompt:** "Build a 4-week and 3-month cash forecast using historical
inflows and outflows. Factor in seasonality from the same period last
year, and highlight any week where projected liquidity falls below our
\$200M minimum threshold."

<div class="lane">

<div class="step">

intake

</div>

<div>

Persona = executive-F, complexity = advanced, intent =
forecast_with_threshold, tribal_needed = true (threshold lives in
tribal).

</div>

</div>

<div class="lane">

<div class="step">

PLAN

</div>

<div>

Plan decomposed into 5 sub-Qs:

1.  **SQ1**: Current investment-book floor (latest snapshot, sum
    marketValue).
2.  **SQ2**: Forward maturity ladder: investment maturities + FX
    value-dates, weekly buckets W0–W13. *Depends on SQ1.*
3.  **SQ3**: Historical inflow profile for same calendar period 2025
    (seasonality reference).
4.  **SQ4**: Tribal lookup — current \$200M minimum-liquidity policy
    threshold (effective today). *Independent.*
5.  **SQ5**: Weekly projection = SQ1 + SQ2 + SQ3-seasonality-overlay;
    flag weeks below SQ4. *Depends on 1, 2, 3, 4.*

</div>

</div>

<div class="lane">

<div class="step">

plan_val

</div>

<div>

DAG acyclic. All ontology terms resolve. Estimated cost \< budget. Pass.

</div>

</div>

<div class="lane">

<div class="step">

ACT · SQ1

</div>

<div>

Inner pipeline runs. SPARQL ok. Result: \$29.9M. Step reflector: pass.

</div>

</div>

<div class="lane">

<div class="step">

ACT · SQ2

</div>

<div>

Inner pipeline runs. First SPARQL attempt fails SHACL — used
`lpp:settlementDate` (doesn't exist). <span class="tag fix">L1
repair</span>: regenerate with `lpp:valueDate`. Pass. Result: 13-week
ladder. Step reflector: pass.

</div>

</div>

<div class="lane">

<div class="step">

ACT · SQ3

</div>

<div>

SPARQL runs but returns 0 rows. Verifier flags: "expected historical
inflows, got empty". <span class="tag fix">L1 repair</span> × 2 still
empty → <span class="tag fix">L2 replan</span> rewrites SQ3 as "do we
have any cash-flow facts in 2025-05 to 2025-08?". L2 SPARQL runs,
returns 0 rows again. Step reflector concludes: **seasonality data is
genuinely not in the graph**. Sub-Q marked `skipped` with reason;
scratchpad records the gap.

</div>

</div>

<div class="lane">

<div class="step">

ACT · SQ4

</div>

<div>

Tribal SPARQL. Returns: \$200M policy, effective 2025-09-01, source =
Treasury Policy 2025-LP-04. Pass.

</div>

</div>

<div class="lane">

<div class="step">

ACT · SQ5

</div>

<div>

Compose: floor \$29.9M + ladder; no seasonality overlay (SQ3 skipped);
flag weeks \< \$200M. Result: 2 weeks below threshold. Step reflector:
pass — explicitly noting the seasonality gap in the result.

</div>

</div>

<div class="lane">

<div class="step">

final_ref

</div>

<div>

Original question asked for forecast *with seasonality*. Assembled
answer has forecast + threshold but seasonality is missing. Reflector:
**partial pass with honest gap** — not a replan trigger because no
further graph data will surface seasonality. Continue.

</div>

</div>

<div class="lane">

<div class="step">

synth

</div>

<div>

Persona = executive-F → one-pager template. "Forecast shows W0–W1 below
\$200M; W2 KRW settlement clears the threshold. Seasonality could not be
modeled — historical cash-flow facts are not in the loaded slice;
recommend federating with Snowflake AR/AP feeds." Citations: SQ1 SPARQL,
SQ2 SPARQL, SQ4 tribal fact URI, SQ3 skip reason.

</div>

</div>

<div class="lane">

<div class="step">

viz + HIL

</div>

<div>

Renders `cfo-demo-report.html`-style HTML. Routes to Treasury Director
for HIL because persona=executive-F + complexity=advanced.

</div>

</div>

<div class="callout good">

This is the differentiator. A single-shot pipeline would have either
hallucinated a seasonality number or returned "no data". The
Plan/Reflect loop **tries**, **recognizes the gap**, **marks it
honestly**, and still delivers an answer that the rest of the question
allows.

</div>

</div>

<div class="section">

## 8 · Parallelism — what runs concurrently

The planner emits a DAG, not a list. The executor processes it with a
topological-sort + ready-queue:

1.  Find all sub-Qs whose dependencies are satisfied.
2.  Fire them concurrently into the inner pipeline (async / Send pattern
    in LangGraph).
3.  As each completes, recompute the ready set.

In the trace above, SQ1 / SQ3 / SQ4 are independent and run in parallel;
SQ2 depends on SQ1; SQ5 waits for all four. Wall-clock for the CFO
briefing drops from ~30s (sequential) to ~8–10s (parallel) for a 4–5
sub-Q plan.

``` code
async def executor(s: S) -> S:
    while not all_done(s["plan"], s["scratchpad"]):
        ready = topo_ready(s["plan"], s["scratchpad"])
        if not ready:
            s["halt_reason"] = "deadlock"; break
        results = await asyncio.gather(*[
            run_inner_pipeline(sq, pack=s["pack"], shared=s)
            for sq in ready
        ])
        for sq, out in zip(ready, results):
            s["scratchpad"][sq["id"]] = out
            s["budget_used"].add(out["cost"])
            if s["budget_used"].over(s["plan"]["budget"]):
                s["halt_reason"] = "budget"; return s
    return s
```

</div>

<div class="section">

## 9 · Pack-level controls (per domain, not per question)

The kernel ships the loop; each pack tunes it. From `pack.yaml`:

``` code
loop:
  max_subqs_per_plan: 8
  max_plan_attempts: 2
  max_l1_per_subq:    2
  max_l2_per_subq:    1
  parallel_fanout:    4
  budget:
    tokens:     150000     # per request
    seconds:     60        # wall clock
    fuseki_rows: 100000
    usd:           0.50
  planner_prompt:    prompts/planner.j2
  reflector_prompt:  prompts/reflector.j2
  repairer_prompts:
    L1: prompts/repair_sparql.j2
    L2: prompts/repair_subq.j2
    L3: prompts/repair_plan.j2
  skip_planner_when:
    - complexity == simple        # don't pay planner overhead on trivial Qs
    - intent in [balance_lookup, code_lookup]
```

Treasury runs with these caps. A higher-stakes domain (e.g. Risk) might
cut `max_plan_attempts` to 1 and require HIL on every L3 replan. A
lower-stakes domain (e.g. Marketing) might raise the budget.

</div>

<div class="section">

## 10 · Observability of the loop

Every loop iteration emits a structured trace. The audit record for one
request looks like:

``` code
{
  "request_id": "req_9f3...",
  "question": "Build a 4-week and 3-month cash forecast...",
  "persona": "executive-F",
  "pack": "treasury@0.3.0",
  "plan_attempts": 1,
  "plan": [
    {"id":"SQ1","status":"done","attempts":1,"l1":0,"l2":0},
    {"id":"SQ2","status":"done","attempts":2,"l1":1,"l2":0,"errors":["SHACL: lpp:settlementDate not in pack"]},
    {"id":"SQ3","status":"skipped","attempts":3,"l1":2,"l2":1,"reason":"no seasonality data in graph"},
    {"id":"SQ4","status":"done","attempts":1,"l1":0,"l2":0},
    {"id":"SQ5","status":"done","attempts":1,"l1":0,"l2":0}
  ],
  "final_reflection": {"pass": true, "gaps": ["seasonality"], "honest_refusal_part": "seasonality"},
  "budget_used": {"tokens": 41200, "seconds": 9.4, "fuseki_rows": 3210, "usd": 0.18},
  "hil": {"required": true, "approver": "treasury_director", "status": "approved"},
  "answer_uri":  "s3://.../answers/req_9f3.html",
  "evidence": ["sparql:SQ1...", "sparql:SQ2...", "tribal:Policy/2025-LP-04"]
}
```

Every step is replayable. Every refusal is explainable. Every model
invocation has a token cost. This is what makes the loop trustworthy
enough to ship.

</div>

<div class="section">

## 11 · What this adds and what stays the same

| Concern | Single-shot pipeline (today) | With Plan/Execute/Reflect loop |
|----|----|----|
| Simple prompts | Works | Bypass loop (`skip_planner_when`) — same latency |
| Complex prompts | Often loses sub-step (e.g. variance decomposition) | Multi-sub-Q DAG, parallel execution |
| Advanced prompts | Hallucinates or refuses wholesale | Decomposes, partial-answers, refuses only on the unanswerable slice |
| SPARQL parse errors | Single retry (already in inner) | L1 repair with explicit error context, capped attempts |
| Semantic wrongness (correct SPARQL, wrong question) | Slips through | step_reflector + L2 replan catches it |
| Out-of-scope facts | Hallucinates or generic refusal | Sub-Q marked `skipped` with reason; rest of answer continues |
| Audit | Single SPARQL + single answer | Full plan, every attempt, every reflection, every repair — replayable |
| Cost control | Per-call token cap | Per-request envelope across all sub-Qs and repairs; circuit breakers |
| Domain-agnosticism | Maintained | Maintained — planner/reflector/repairer prompts live in the pack, kernel ships the wiring only |

<div class="callout info">

**Headline.** Yes — the architecture plans, loops, validates, and
corrects. The kernel ships the five outer-loop nodes (planner,
plan_validator, executor, step_reflector, repairer, final_reflector).
The inner 14-node pipeline runs once per sub-question. Three escalation
levels (L1 SPARQL repair, L2 sub-Q replan, L3 plan replan) plus hard
budget circuit breakers and an honest-refusal terminal state. Domain
packs configure the loop's budget and prompts; they don't reimplement
it.

</div>

</div>

Companion to [architecture.html](architecture.html) (treasury view),
[architecture-domain-agnostic.html](architecture-domain-agnostic.html)
(kernel + packs), and [cfo-demo-report.html](cfo-demo-report.html) (the
working demo this design targets).
