"""arq worker definitions.

Heavy scrapes (browser-based) go through arq so the API stays responsive and
we can horizontally scale workers (`docker compose up --scale worker=4`).
"""

from arq.connections import RedisSettings

from pricepulse.config import get_settings


async def scrape_source(ctx: dict, source: str, query: str, max_per_source: int) -> dict:
    """Wraps a single-source scrape as an arq job; result is fetched via job_id."""
    # TODO (hackathon): import the right adapter and invoke `.search(...)`,
    # then return ScrapeResult.model_dump().
    return {"source": source, "offers": [], "query": query, "limit": max_per_source}


class WorkerSettings:
    settings = get_settings()
    functions = [scrape_source]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
