import pytest
from httpx import ASGITransport, AsyncClient

# Reuse the single app built at import time. Calling create_app() per test
# re-registers the Prometheus instrumentator metrics into the global
# CollectorRegistry and raises "Duplicated timeseries".
from pricepulse.main import app


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
