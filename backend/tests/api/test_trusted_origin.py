from httpx import AsyncClient

ALLOWED_ORIGIN = "http://localhost:3000"


async def write(client: AsyncClient, headers: dict[str, str]):
    """发一个写请求（新建会话）。先清掉客户端默认带的 Origin，只用这里给的请求头。"""
    client.headers.pop("Origin", None)
    return await client.post("/api/conversations", headers=headers)


async def test_write_from_configured_frontend_is_allowed(client: AsyncClient) -> None:
    response = await write(client, {"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 201


async def test_write_from_other_site_is_rejected_and_writes_nothing(client: AsyncClient) -> None:
    response = await write(client, {"Origin": "http://evil.example.com"})
    conversations = (await client.get("/api/conversations")).json()

    assert response.status_code == 403
    assert conversations == []


async def test_write_without_origin_falls_back_to_referer(client: AsyncClient) -> None:
    allowed = await write(client, {"Referer": f"{ALLOWED_ORIGIN}/some/page"})
    rejected = await write(client, {"Referer": "http://evil.example.com/page"})

    assert allowed.status_code == 201
    assert rejected.status_code == 403


async def test_write_without_origin_or_referer_is_rejected(client: AsyncClient) -> None:
    response = await write(client, {})

    assert response.status_code == 403


async def test_write_from_backend_own_page_is_allowed(client: AsyncClient) -> None:
    response = await write(client, {"Origin": "http://test"})

    assert response.status_code == 201


async def test_reads_do_not_need_origin(client: AsyncClient) -> None:
    client.headers.pop("Origin", None)

    response = await client.get("/api/conversations")

    assert response.status_code == 200
