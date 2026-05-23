"""Admin endpoints: serve the static `admin/index.html` landing page and
expose a thin /admin/links JSON for the frontend to render its own variant.

Served as a fallback in case `gethomepage/homepage:3030` is not running.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from pricepulse.api.deps import SettingsDep
from pricepulse.core.features import FeatureFlags

router = APIRouter(tags=["meta"])

_ROOT = Path(__file__).resolve().parents[4]   # backend/
_INDEX = _ROOT / "admin" / "index.html"


@router.get("/admin", include_in_schema=False)
async def admin_landing() -> FileResponse:
    return FileResponse(_INDEX, media_type="text/html")


@router.get("/admin/features")
async def admin_features(settings: SettingsDep) -> JSONResponse:
    """Effective feature-flag state — useful for the demo board and CI."""
    return JSONResponse(FeatureFlags.from_settings(settings).summary())


@router.get("/admin/links")
async def admin_links(settings: SettingsDep) -> JSONResponse:
    """Machine-readable list of admin URLs. Host comes from ``ADMIN_HOST``
    so the same code serves dev (localhost) and prod (internal hostname).
    Ports are docker-compose host-port mappings and stay inline."""
    host = settings.admin_host.rstrip("/")

    def link(name: str, port: int, path: str = "") -> dict:
        return {"name": name, "url": f"{host}:{port}{path}", "port": port}

    return JSONResponse(
        {
            "core": [
                link("FastAPI", settings.api_port, "/docs"),
                link("n8n", 5678),
                link("SearXNG", 8080),
                link("Spellcheck (SAGE)", 8095, "/health"),
            ],
            "observability": [
                link("Grafana", 3000),
                link("Prometheus", 9090),
                link("Dozzle", 8888),
                link("Uptime Kuma", 3001),
                link("GlitchTip", 8001),
                link("ntfy", 8090),
                link("Apprise", 8082),
            ],
            "storage": [
                link("pgAdmin", 5050),
                link("MinIO", 9001),
            ],
            "antibot": [
                {"name": "Ollama (Gemma 4)", "url": settings.ollama_url},
            ],
        }
    )
