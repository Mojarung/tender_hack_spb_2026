from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "dev", "prod"] = "local"
    app_log_level: str = "INFO"

    api_host: str = "0.0.0.0"  # noqa: S104
    api_port: int = 8000
    api_cors_origins: str = "http://localhost:3000"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "pricepulse"
    postgres_password: str = "changeme"
    postgres_db: str = "pricepulse"

    redis_url: str = "redis://redis:6379/0"

    proxy_pool_residential: str = ""
    proxy_pool_datacenter: str = ""

    ozon_browser_pool: int = 2
    yandex_market_browser_pool: int = 2

    wb_rpm: int = 60
    ozon_rpm: int = 20
    yandex_market_rpm: int = 10
    runet_rpm: int = 30

    twocaptcha_api_key: str = ""

    # L3 fallback — foreign services (free tiers)
    scrapfly_api_key: str = ""
    apify_api_token: str = ""
    zenrows_api_key: str = ""
    firecrawl_api_key: str = ""

    firecrawl_url: str = "http://firecrawl-api:3002"
    searxng_url: str = "http://searxng:8080"

    # LLM extraction for the 4th source
    gemini_api_key: str = ""
    deepseek_api_key: str = ""

    # S3 / MinIO — image cache
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "pricepulse"
    s3_secret_key: str = "pricepulse_dev_password"
    s3_bucket: str = "pricepulse-images"
    s3_region: str = "us-east-1"

    # Local LLM (Gemma 4 via Ollama)
    ollama_url: str = "http://ollama:11434"
    ollama_vision_model: str = "gemma4:e4b"

    # Notifications
    ntfy_url: str = "http://ntfy/pricepulse-alerts"
    apprise_url: str = "http://apprise:8000/notify/pricepulse"

    # Admin services
    pgadmin_password: str = "hackathon"
    glitchtip_secret_key: str = ""

    # ===== Feature flags (free-mode by default) =====
    # Global killswitch. If false, no paid branch is ever taken.
    features_allow_paid: bool = False
    # Granular flags — only effective when allow_paid is true.
    feature_use_paid_proxies: bool = False
    feature_use_2captcha: bool = False
    feature_use_paid_llm: bool = False
    feature_use_paid_l3: bool = False

    # Demo mode — pre-warm Redis cache for jury-known queries so live demo
    # is sub-100ms with no risk of bans.
    demo_mode: bool = False

    # Cost guard (USD hard-cap for 24h of L3 + L4). $0 in free-mode.
    cost_cap_usd: int = 0

    # ===== Auth =====
    # JWT signing key — MUST be set in production. Empty default lets dev work,
    # `openssl rand -hex 32` to generate.
    auth_jwt_secret: str = "dev-only-change-in-prod-pricepulse-jwt-secret"
    auth_jwt_lifetime_seconds: int = 24 * 3600    # 24h

    # ===== Dev DB fallback =====
    # Override Postgres entirely with a SQLite DSN when set — useful for
    # `uv run uvicorn ...` without a running Postgres container.
    sqlite_url: str = ""    # e.g. "sqlite+aiosqlite:///./pricepulse.db"

    @property
    def database_url(self) -> str:
        """The actual DSN SQLAlchemy uses. SQLite override wins for dev."""
        return self.sqlite_url or self.postgres_dsn

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def residential_proxies(self) -> list[str]:
        return [p.strip() for p in self.proxy_pool_residential.split(",") if p.strip()]

    @property
    def datacenter_proxies(self) -> list[str]:
        return [p.strip() for p in self.proxy_pool_datacenter.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


__all__ = ["Settings", "get_settings"]
