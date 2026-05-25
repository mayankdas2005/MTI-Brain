"""AWS Bedrock LLM initialization for the MTI Brain pipeline.

Provides three model tiers:
  fast     → Haiku   (classify, step_reflector, ontology_lookup)
  balanced → Sonnet  (sparql_gen, graph_reasoning, synthesis, plan, reflectors)
  deep     → Opus    (L3 plan repairer, escalated SPARQL repair)

Call ``init_llms()`` once at startup, then ``get_llm(tier)`` from any node.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logger import logger
from langchain_aws import ChatBedrock

_llm_map: dict[str, ChatBedrock] = {}


def _region_from_arn(arn: str) -> str:
    """Extract AWS region from a Bedrock inference-profile ARN."""
    if isinstance(arn, str) and arn.startswith("arn:aws:bedrock:"):
        parts = arn.split(":", 5)
        if len(parts) >= 4 and parts[3]:
            return parts[3]
    return settings.AWS_REGION


def _build_llm(model_arn: str) -> ChatBedrock:
    region = _region_from_arn(model_arn)
    return ChatBedrock(
        model=model_arn,
        provider="anthropic",
        api_key=settings.AWS_BEARER_TOKEN_BEDROCK or None,
        region=region,
        streaming=True,
        model_kwargs={"temperature": 0.0, "max_tokens": 8192},
    )


def init_llms() -> None:
    """Build and cache the fast/balanced/deep LLM instances.

    Falls back gracefully: if a tier ARN is not configured, that tier uses
    the balanced (Sonnet) model so the pipeline still runs with partial config.
    """
    global _llm_map

    sonnet_arn = settings.AWS_BEDROCK_SONNET_ARN
    haiku_arn = settings.AWS_BEDROCK_HAIKU_ARN or sonnet_arn
    opus_arn = settings.AWS_BEDROCK_OPUS_ARN or sonnet_arn

    if not sonnet_arn:
        raise RuntimeError(
            "AWS_BEDROCK_SONNET_ARN must be set — cannot initialize pipeline LLMs."
        )

    balanced = _build_llm(sonnet_arn)
    fast = _build_llm(haiku_arn) if haiku_arn != sonnet_arn else balanced
    deep = _build_llm(opus_arn) if opus_arn != sonnet_arn else balanced

    _llm_map = {"fast": fast, "balanced": balanced, "deep": deep}

    def _short(arn: str) -> str:
        return arn.split("/")[-1] if "/" in arn else arn.split(":")[-1]

    logger.info(
        f"Bedrock LLMs ready | "
        f"fast={'Haiku (' + _short(haiku_arn) + ')' if haiku_arn != sonnet_arn else 'FALLBACK→Sonnet (set AWS_BEDROCK_HAIKU_ARN)'} | "
        f"balanced=Sonnet ({_short(sonnet_arn)}) | "
        f"deep={'Opus (' + _short(opus_arn) + ')' if opus_arn != sonnet_arn else 'FALLBACK→Sonnet'}"
    )


def get_llm(tier: str = "balanced") -> ChatBedrock:
    """Return the LLM for the requested tier. Falls back to balanced."""
    if not _llm_map:
        raise RuntimeError("LLMs not initialized — call init_llms() first.")
    return _llm_map.get(tier) or _llm_map["balanced"]
