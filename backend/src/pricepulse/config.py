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

    firecrawl_url: str = "http://firecrawl-api:3002"
    firecrawl_api_key: str = ""
    searxng_url: str = "http://searxng:8080"

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
