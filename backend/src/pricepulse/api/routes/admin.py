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
async def admin_links() -> JSONResponse:
    """Machine-readable list of admin URLs — used by the SPA frontend."""
    return JSONResponse(
        {
            "core": [
                {"name": "FastAPI", "url": "http://localhost:8000/docs", "port": 8000},
                {"name": "n8n", "url": "http://localhost:5678", "port": 5678},
                {"name": "Firecrawl", "url": "http://localhost:3002", "port": 3002},
                {"name": "SearXNG", "url": "http://localhost:8080", "port": 8080},
            ],
            "observability": [
                {"name": "Grafana", "url": "http://localhost:3000", "port": 3000},
                {"name": "Prometheus", "url": "http://localhost:9090", "port": 9090},
                {"name": "Dozzle", "url": "http://localhost:8888", "port": 8888},
                {"name": "Uptime Kuma", "url": "http://localhost:3001", "port": 3001},
                {"name": "GlitchTip", "url": "http://localhost:8001", "port": 8001},
                {"name": "ntfy", "url": "http://localhost:8090", "port": 8090},
                {"name": "Apprise", "url": "http://localhost:8082", "port": 8082},
            ],
            "storage": [
                {"name": "pgAdmin", "url": "http://localhost:5050", "port": 5050},
                {"name": "MinIO", "url": "http://localhost:9001", "port": 9001},
            ],
            "antibot": [
                {"name": "Ollama (Gemma 4)", "url": "http://localhost:11434", "port": 11434},
                {"name": "2Captcha", "url": "https://2captcha.com/enterpage"},
            ],
        }
    )
