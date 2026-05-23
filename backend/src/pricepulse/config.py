from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "dev", "prod"] = "local"
    app_log_level: str = "INFO"

    api_host: str = "0.0.0.0"  # noqa: S104
    api_port: int = 8000
    api_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://85.193.89.114:3000"

    # Base host the browser uses to reach our admin landing page links.
    # Override per-environment (e.g. https://admin.pricepulse.team in prod).
    admin_host: str = "http://localhost"

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
    # L2 stealth browser (nodriver). Headless is server-friendly; flip to
    # false on a desktop/xvfb box for the strongest anti-detect profile.
    browser_headless: bool = True

    # Persistent Chrome profile for the Ozon stealth browser. Cookies
    # (abt_data, __Secure-ext_xcid, etc.) survive container restarts so
    # the first-request anti-bot challenge is paid once per profile,
    # not once per process. Bind a Docker volume here in prod.
    ozon_profile_dir: str = "var/profiles/ozon"
    # How long warmed cookies stay valid in-process before we re-launch
    # the browser to refresh them. Empirically Ozon rotates these in
    # 24–72 h; 12 h is a safe default.
    ozon_cookie_ttl_sec: int = 12 * 3600
    # Override the Chrome binary path if auto-detect picks the wrong one
    # (e.g. Yandex Browser on the dev machine).
    ozon_browser_path: str = ""

    # Persistent Chrome profile for the WB stealth browser (separate
    # from Ozon's). DOM-scrape of wildberries.ru's SSR catalog page
    # needs an isolated profile so cookies don't bleed between
    # marketplaces.
    wb_profile_dir: str = "var/profiles/wb"
    wb_browser_path: str = ""
    # Per-offer concurrent enrichment limit (card.json + feedbacks v2).
    wb_enrich_concurrency: int = 5
    # Reviews per offer (each carries photos + video URLs).
    wb_reviews_per_offer: int = 10

    # WB rpm намеренно снижен по результатам experiments/wb_research:
    # safe-rate probe ловил 429 уже на 6 RPM с одного IP, поэтому
    # держим бюджет консервативно низким.
    wb_rpm: int = 6
    ozon_rpm: int = 20
    yandex_market_rpm: int = 10
    runet_rpm: int = 30

    # 4th source — self-hosted SearXNG (URL discovery only; parsing is on us).
    # The methodology (final_presa.pdf p.5) bans external scraping APIs and
    # search-engine APIs, so the previous Firecrawl-cloud / Scrapfly / Apify /
    # ZenRows / Gemini / DeepSeek keys are gone wholesale.
    searxng_url: str = "http://searxng:8080"

    # Spell-correction microservice — SAGE FRED-T5 distilled-95M (Sber)
    # at backend/spellcheck/. Empty disables it; normalize_query then runs
    # without general-RU spell correction (brand thesaurus + pymorphy3 still work).
    spellcheck_url: str = ""

    # S3 / MinIO — image cache
    s3_endpoint_url: str = "http://minio:9000"
    # Public URL the browser sees when we 302 to a cached image. In dev with
    # MinIO it's usually the same as the endpoint; in prod it'd be a CDN
    # hostname in front of MinIO/S3. Empty falls back to ``s3_endpoint_url``.
    s3_public_url: str = ""
    s3_access_key: str = "pricepulse"
    s3_secret_key: str = "pricepulse_dev_password"
    s3_bucket: str = "pricepulse-images"
    s3_region: str = "us-east-1"
    # Empty disables the image-proxy/cache layer — ProductCard then loads
    # straight from marketplace CDNs. Tests / minimal demos can run without
    # MinIO this way.
    image_cache_enabled: bool = True

    # Local LLM (Gemma 4 via Ollama)
    ollama_url: str = "http://ollama:11434"
    ollama_api_key: str = ""
    ollama_vision_model: str = "gemma4:e4b"
    ollama_text_model: str = "qwen3.5:9b"

    # Demo mode — pre-warm Redis cache for jury-known queries so the live
    # demo answers in <100 ms. Paid feature flags / cost-guard are gone —
    # nothing in the project hits a paid third-party service any more.
    demo_mode: bool = False

    # ===== Auth =====
    # JWT signing key — MUST be set in production. Empty default lets dev work,
    # `openssl rand -hex 32` to generate.
    auth_jwt_secret: str = "dev-only-change-in-prod-pricepulse-jwt-secret"
    auth_jwt_lifetime_seconds: int = 24 * 3600    # 24h

    # ===== Dev DB fallback =====
    # Override Postgres entirely with a SQLite DSN when set — useful for
    # `uv run uvicorn ...` without a running Postgres container.
    sqlite_url: str = ""    # e.g. "sqlite+aiosqlite:///./pricepulse.db"

    @model_validator(mode="after")
    def _prod_must_override_secrets(self) -> "Settings":
        # Hackathon defense runs APP_ENV=local — this only bites in real deployments,
        # where shipping with the dev JWT secret would let anyone mint admin tokens.
        if self.app_env == "prod" and self.auth_jwt_secret.startswith("dev-only"):
            raise ValueError(
                "AUTH_JWT_SECRET is at its dev default in APP_ENV=prod — "
                "generate one with `openssl rand -hex 32` and set it in env."
            )
        return self

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
