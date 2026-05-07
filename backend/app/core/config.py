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
from pydantic import Field, field_validator
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
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str
    POSTGRES_DB: str

    # ── Database config (config.yml) ──────────────────────────────────────────
    POSTGRES_PORT: int = Field(default=_db.get("port", 5432))
    DATABASE_SSL_MODE: str = Field(default=_db.get("ssl_mode", "disable"))
    DATABASE_SSL_ROOT_CERT: str = Field(default=_db.get("ssl_root_cert", ""))

    # ── Connection pool (config.yml) ──────────────────────────────────────────
    DB_POOL_SIZE: int = Field(default=_pool.get("size", 10))
    DB_MAX_OVERFLOW: int = Field(default=_pool.get("max_overflow", 20))
    DB_POOL_RECYCLE: int = Field(default=_pool.get("recycle_seconds", 1800))
    DB_POOL_TIMEOUT: int = Field(default=_pool.get("timeout_seconds", 30))

    # ── Circuit breaker (config.yml) ──────────────────────────────────────────
    CB_FAIL_MAX: int = Field(default=_cb.get("fail_max", 5))
    CB_RESET_TIMEOUT: int = Field(default=_cb.get("reset_timeout_seconds", 30))

    # ── JWT config (config.yml) + secret (.env) ───────────────────────────────
    JWT_ALGORITHM: str = Field(default=_jwt.get("algorithm", "HS256"))
    JWT_EXPIRY_HOURS: int = Field(default=_jwt.get("expiry_hours", 8))
    JWT_SECRET: str = Field(..., min_length=32)

    # ── Rate limiting (config.yml) ────────────────────────────────────────────
    RATE_LIMIT_LOGIN_PER_MINUTE: int = Field(default=_rl.get("login_per_minute", 5))

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
