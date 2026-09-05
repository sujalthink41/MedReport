from httpx import AsyncClient


async def test_health_reports_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "MedReport"


async def test_ready_returns_checks(client: AsyncClient) -> None:
    response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    assert "checks" in response.json()


async def test_docs_are_available_outside_production(client: AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200
