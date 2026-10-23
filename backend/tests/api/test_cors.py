from httpx import AsyncClient

ALLOWED_ORIGIN = "http://localhost:3000"
UNLISTED_ORIGIN = "http://unlisted.example.com"


async def test_configured_frontend_passes_preflight_for_json_post_with_cookies(
    client: AsyncClient,
) -> None:
    response = await client.options(
        "/api/conversations/1/messages",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_unlisted_origin_gets_no_cross_origin_permission(client: AsyncClient) -> None:
    response = await client.get("/api/conversations", headers={"Origin": UNLISTED_ORIGIN})

    assert "access-control-allow-origin" not in response.headers
