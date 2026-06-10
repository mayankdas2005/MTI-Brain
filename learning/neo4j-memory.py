# %%
import os
from dotenv import load_dotenv

load_dotenv(r"../backend/.env")

# %%
NEO4J_URI=os.getenv("NEO4J_URI")
NEO4J_USER=os.getenv("NEO4J_USER")
NEO4J_PASSWORD=os.getenv("NEO4J_PASSWORD")
NEO4J_DB="graphacademy"

AWS_REGION=os.getenv("AWS_REGION")
AWS_BEARER_TOKEN_BEDROCK=os.getenv("AWS_BEARER_TOKEN_BEDROCK")

AWS_BEDROCK_HAIKU_ARN=os.getenv("AWS_BEDROCK_HAIKU_ARN")
AWS_BEDROCK_COHERE_EMBED_V4_ARN=os.getenv("AWS_BEDROCK_COHERE_EMBED_V4_ARN")

# %%
from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.config import Neo4jConfig
from neo4j_agent_memory.config import Neo4jConfig, EmbeddingConfig, LLMConfig, ExtractionConfig, ExtractorType
from  neo4j_agent_memory.extraction import ExtractorBuilder

# %%
from neo4j_agent_memory.llm.adapters.litellm import LiteLLMProvider, LiteLLMEmbeddingProvider  

extractor = ExtractorBuilder().with_spacy(model="en_core_web_sm").with_gliner().merge_by_confidence().build()
  
settings = MemorySettings(  
    neo4j=Neo4jConfig(
        uri=NEO4J_URI,
        username=NEO4J_USER,
        password=NEO4J_PASSWORD,
        database=NEO4J_DB,
    ),  
    llm=LiteLLMProvider(  
        model=f"bedrock/{AWS_BEDROCK_HAIKU_ARN}",   
        api_key=AWS_BEARER_TOKEN_BEDROCK,
        aws_region=AWS_REGION,  
    ),  
    embedding=LiteLLMEmbeddingProvider(  
        model=f"bedrock/{AWS_BEDROCK_COHERE_EMBED_V4_ARN}",  
        api_key=AWS_BEARER_TOKEN_BEDROCK,  
        aws_region=AWS_REGION,  
        dimensions=1536,  
    ),
    extraction=ExtractionConfig(  
        extractor_type=ExtractorType.PIPELINE,  # No spaCy or GLiNER
        enable_spacy=True,
        spacy_model="en_core_web_lg",
        enable_gliner=True,
        gliner_model="gliner-community/gliner_large-v2.5",
        enable_llm_fallback=False,
    ),  
)

# %%
user_message = """Show me the breakdown of cross-border payment volume by corridor, currency, and method for the past 12 months, with end-to-end cost including FX spread, lifting fees, and correspondent charges."""


asst_message = """"Data Quality Concern
Two data integrity issues compromise this analysis and must be resolved before any cost findings are acted upon.

Invalid origination country code 'HO' appears across multiple rows. ISO 3166-1 alpha-2 does not recognize this code — corridor routing and origination country segmentation are unreliable wherever this code appears, meaning any corridor-level cost comparison may be misattributed.

Future-dated records: the dataset includes transactions dated August 2026, which is 58 days ahead of today (2026-06-09). Whether these represent forecasted, test, or misdated records is unknown — their inclusion in volume and cost aggregates cannot be validated until the source is confirmed.

All findings below are framed pending resolution of these concerns.

Verdict
The end-to-end cost breakdown you requested cannot be delivered: correspondent charges and total cost figures are entirely absent across all 2,495,075 transactions — and at industry-standard rates, this blind spot likely understates true payment friction by $100M+ annually.

What This Means
Critical cost data is missing, not just incomplete — correspondent_charges_amount and end_to_end_cost_monetary are NULL across every one of the 2,495,075 rows in the dataset. Correspondent charges typically represent 15–25% of total cross-border payment cost; omitting them means every cost-per-corridor figure currently in use is structurally understated. The $100M+ annual understatement is an industry-standard estimate applied to this dataset — the actual figure cannot be confirmed until the fields are populated.
Lifting fee variability signals an unmanaged cost spread — fees range from $16.20 to $4,219.13 per transaction, with a mean of $2,780.53 and median of $3,235.29. The Turkey-to-Hong Kong corridor carries the highest observed fees at $3,723.37, versus $2,650.30 for Korea-to-Singapore — a $1,073 per-transaction gap that, at scale, represents a material and likely unoptimized cost differential across corridors.

FX spread adds further uneven cost pressure — spreads range from 33.0 to 79.0 basis points, with a mean of 51.68 bps. The 46 bps spread between the tightest and widest corridor is not a rounding difference; on large-value cross-border flows, this range determines whether a corridor is competitively priced or quietly expensive.

Decision
Direct Data Engineering to identify why correspondent_charges_amount and end_to_end_cost_monetary are NULL across all 2,495,075 rows, and restore or source these fields within 10 business days.

If actioned: full end-to-end cost visibility is restored across the entire transaction population, enabling defensible corridor-level cost benchmarking and fee negotiation with correspondent banks.

If deferred: every corridor cost decision — pricing, routing, and bank relationship reviews — continues to be made against an incomplete cost base that structurally understates true payment friction, with the $100M+ annual gap remaining invisible to management."""

# %%
import asyncio
import time
import uuid
import logging
import inspect
from datetime import datetime

# Single logger used for everything — timing + debug
log = logging.getLogger("neo4j_memory_test")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d %(name)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("neo4j-memory-debug.log", mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)


async def main():
    log.info("=== RUN STARTED at %s ===", datetime.now().isoformat())

    session_id = f"test_{uuid.uuid4().hex[:8]}"
    log.info("Session: %s", session_id)

    # --- Time the MemoryClient initialization separately ---
    t_init_start = time.perf_counter()
    log.info("[START] MemoryClient init (__aenter__)")
    memory_client = MemoryClient(settings)
    memory = await memory_client.__aenter__()
    t_init_done = time.perf_counter()
    log.info("[DONE]  MemoryClient init: %.3fs", t_init_done - t_init_start)

    try:
        t0 = time.perf_counter()
        log.info("[START] add_message (user)")
        await memory.short_term.add_message(
            session_id=session_id,
            role="user",
            extract_entities=True,
            extract_relations=True,
            content=user_message
        )
        t1 = time.perf_counter()
        log.info("[DONE]  add_message (user):      %.3fs", t1 - t0)

        log.info("[START] add_message (assistant)")
        await memory.short_term.add_message(
            session_id=session_id,
            role="assistant",
            extract_entities=True,
            extract_relations=True,
            content=asst_message
        )
        t2 = time.perf_counter()
        log.info("[DONE]  add_message (assistant): %.3fs", t2 - t1)

        log.info("[START] get_conversation")
        conversation = await memory.short_term.get_conversation(session_id)
        t3 = time.perf_counter()
        log.info("[DONE]  get_conversation:        %.3fs", t3 - t2)

        log.info("[START] search_messages")
        results = await memory.short_term.search_messages(
            query="cross border payment cost breakdown",
            session_id=session_id,
            limit=10
        )
        t4 = time.perf_counter()
        log.info("[DONE]  search_messages:         %.3fs", t4 - t3)

        log.info("--- Total (ops only): %.3fs ---", t4 - t0)
        log.info("--- Total (incl init): %.3fs ---", t4 - t_init_start)

        for msg in conversation.messages:
            log.info("  [%s] %s...", msg.role, msg.content[:80])
        log.info("Search results: %d message(s) found", len(results))
    finally:
        await memory_client.__aexit__(None, None, None)


# %%
if __name__ == "__main__":
    t_start = time.perf_counter()
    asyncio.run(main())
    t_end = time.perf_counter()
    log.info("=== Script total: %.3fs ===", t_end - t_start)
