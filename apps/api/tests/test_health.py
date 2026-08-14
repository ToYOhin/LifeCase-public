"""API foundation smoke tests."""

import asyncio

from httpx import ASGITransport, AsyncClient, Response

from lifecase.main import app


def get(path: str) -> Response:
    """Issue a request directly against the ASGI app without a live server."""

    async def request() -> Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_health_endpoint() -> None:
    response = get("/api/v1/system/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "lifecase-api",
        "version": "0.2.0",
    }


def test_openapi_exposes_health_endpoint() -> None:
    response = get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/system/health" in response.json()["paths"]
