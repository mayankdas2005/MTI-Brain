"""Application configuration.

Non-secret settings are read from ``config.yml`` (committed to git).
Secrets (DB credentials, JWT secret, API keys) are read from ``.env`` (never committed).

Priority order (highest to lowest):
    1. Environment variables
    2. .env file
    3. config.yml defaults
    4. Field defaults in this module

Any config.yml value can be overridden at deploy-time by setting the matching
environment variable — no file edit required.
"""

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_YML = BASE_DIR / "config.yml"


def _load_yml() -> dict[str, Any]:
    if _CONFIG_YML.exists():
        with open(_CONFIG_YML, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


_yml = _load_yml()
_app = _yml.get("app", {})
_server = _yml.get("server", {})
_db = _yml.get("database", {})
_pool = _db.get("pool", {})
_cb = _yml.get("circuit_breaker", {})
_jwt = _yml.get("jwt", {})
_rl = _yml.get("rate_limit", {})
_mdl_rt = _yml.get("model_routing", {})
_prompt_cache = _yml.get("prompt_cache", {})
_fuseki = _yml.get("fuseki", {})
_tribal = _yml.get("tribal_graph", {})
_pipeline = _yml.get("pipeline", {})


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = Field(default=_app.get("name", "MTI Brain Backend"))
    ENVIRONMENT: str = Field(default=_app.get("environment", "development"))
    DEBUG: bool = Field(default=_app.get("debug", False))
    LOG_LEVEL: str = Field(default=_app.get("log_level", "INFO"))

    # ── Server ───────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: list(_server.get("cors_origins", []))
    )

    # ── Database secrets (.env) ───────────────────────────────────────────────
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = Field(..., repr=False)
    POSTGRES_HOST: str
    POSTGRES_DB: str = Field(default="postgres")
    POSTGRES_SCHEMA: str = Field(default="public")

    # ── Database config (config.yml) ──────────────────────────────────────────
    POSTGRES_PORT: int = Field(default=_db.get("port", 5432))
    DATABASE_SSL_MODE: str = Field(default=_db.get("ssl_mode", "disable"))
    DATABASE_SSL_ROOT_CERT: str = Field(default=_db.get("ssl_root_cert", ""))

    # ── Connection pool (config.yml) ──────────────────────────────────────────
    # These values are tuned for PgBouncer TRANSACTION pooling mode.
    # SQLAlchemy's pool manages cheap CLIENT→PgBouncer sockets, not Postgres
    # server connections — keep pool_size small (1-2 per worker).
    # pool_recycle MUST be below PgBouncer SERVER_IDLE_TIMEOUT (default 600 s)
    # to avoid checking out a socket whose server connection PgBouncer already closed.
    DB_POOL_SIZE: int = Field(default=_pool.get("size", 2))
    DB_MAX_OVERFLOW: int = Field(default=_pool.get("max_overflow", 8))
    DB_POOL_RECYCLE: int = Field(default=_pool.get("recycle_seconds", 500))
    DB_POOL_TIMEOUT: int = Field(default=_pool.get("timeout_seconds", 30))

    # ── Circuit breaker (config.yml) ──────────────────────────────────────────
    CB_FAIL_MAX: int = Field(default=_cb.get("fail_max", 5))
    CB_RESET_TIMEOUT: int = Field(default=_cb.get("reset_timeout_seconds", 30))

    # ── JWT config (config.yml) + secret (.env) ───────────────────────────────
    JWT_ALGORITHM: str = Field(default=_jwt.get("algorithm", "HS256"))
    JWT_EXPIRY_HOURS: int = Field(default=_jwt.get("expiry_hours", 8))
    JWT_SECRET: str = Field(..., min_length=32, repr=False)

    # ── Rate limiting (config.yml) ────────────────────────────────────────────
    RATE_LIMIT_LOGIN_PER_MINUTE: int = Field(default=_rl.get("login_per_minute", 5))
    RATE_LIMIT_ASK_PER_MINUTE: int = Field(default=_rl.get("ask_per_minute", 30))

    # ── AWS credentials (.env) ────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = Field(default="")
    AWS_SECRET_ACCESS_KEY: str = Field(default="", repr=False)
    AWS_BOTO3_BUCKET_NAME: str = Field(default="")

    # ── AWS Bedrock config (.env) ─────────────────────────────────────────────
    AWS_BEARER_TOKEN_BEDROCK: str = Field(default="")
    AWS_REGION: str = Field(default="us-west-2")
    AWS_BEDROCK_SONNET_ARN: str = Field(default="")
    AWS_BEDROCK_HAIKU_ARN: str = Field(default="")
    AWS_BEDROCK_OPUS_ARN: str = Field(default="")
    AWS_BEDROCK_COHERE_EMBED_V4_ARN: str = Field(default="")

    # ── Model routing (config.yml) ───────────────────────────────────────────
    LLM_ROUTING_ENABLED: bool = Field(default=_mdl_rt.get("llm_routing_enabled", True))

    # ── Prompt cache (config.yml) ───────────────────────────────────────────
    AWS_BEDROCK_PROMPT_CACHE: bool = Field(default=_prompt_cache.get("aws_bedrock_prompt_cache", False))

    # ── Fuseki / KG (config.yml + .env) ─────────────────────────────
    FUSEKI_URL: str
    FUSEKI_DATASET: str = Field(default="dataset")
    FUSEKI_TIMEOUT: int = Field(default=_fuseki.get("timeout_seconds", 30))
    FUSEKI_USER: str = Field(default="")
    FUSEKI_PASSWORD: str = Field(default="", repr=False)
    
    # ── Langfuse observability (.env) ────────────────────────────────────────
    LANGFUSE_ENABLED: bool = Field(default=False)
    LANGFUSE_PUBLIC_KEY: str = Field(default="")
    LANGFUSE_SECRET_KEY: str = Field(default="", repr=False)
    LANGFUSE_HOST: str = Field(default="https://cloud.langfuse.com")

    # TRIBAL_GRAPH_URL: str = Field(default="http://localhost:3030")
    # TRIBAL_GRAPH_DATASET: str = Field(default="dataset")

    # ── Pipeline (config.yml) ─────────────────────────────────────────────────
    PIPELINE_RECURSION_LIMIT: int = Field(default=_pipeline.get("recursion_limit", 80))

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=False,
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            return [origin.strip() for origin in v.split(",")]
        return v

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v):
        allowed = {"development", "production"}
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}")
        return v

    @field_validator("DATABASE_SSL_MODE")
    @classmethod
    def validate_ssl_mode(cls, v):
        allowed = {"disable", "require", "verify-ca", "verify-full"}
        value = v.lower()
        if value not in allowed:
            raise ValueError(f"DATABASE_SSL_MODE must be one of {allowed}")
        return value

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if v == "change-me-in-production":
            raise ValueError(
                "JWT_SECRET must be set to a real secret, not the placeholder default"
            )
        return v

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.ENVIRONMENT == "production" and self.DATABASE_SSL_MODE == "disable":
            raise ValueError(
                "DATABASE_SSL_MODE cannot be 'disable' in production. "
                "Set DATABASE_SSL_MODE=require or verify-full."
            )
        return self

    @property
    def DATABASE_URL(self) -> str:
        encoded_password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+asyncpg://"
            f"{self.POSTGRES_USER}:{encoded_password}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/"
            f"{self.POSTGRES_DB}"
        )

    @property
    def CHECKPOINT_CONNINFO(self) -> str:
        return (
            f"host={self.POSTGRES_HOST} "
            f"port={self.POSTGRES_PORT} "
            f"dbname={self.POSTGRES_DB} "
            f"user={self.POSTGRES_USER} "
            f"password={self.POSTGRES_PASSWORD}"
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


settings = Settings()
