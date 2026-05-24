from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_search_empty_groups(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/search", json={"query": "iphone 15", "max_per_source": 5}
    )
    assert response.status_code == 200
    body = response.json()
    # Orchestrator/search.py registry: WB + Ozon + Runet + Google.
    # Я.Маркет still commented out — needs a residential RU proxy pool.
    assert {g["source"] for g in body["groups"]} == {"wb", "ozon", "runet", "google"}
