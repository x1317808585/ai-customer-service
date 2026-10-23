from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from httpx import AsyncClient

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]


async def create_conversation(client: AsyncClient) -> int:
    response = await client.post("/api/conversations")
    assert response.status_code == 201
    return response.json()["id"]


async def test_new_conversation_is_listed_only_for_its_owner(open_client: OpenClient) -> None:
    async with open_client(as_user="alice") as alice, open_client(as_user="bob") as bob:
        conversation_id = await create_conversation(alice)

        alice_list = (await alice.get("/api/conversations")).json()
        bob_list = (await bob.get("/api/conversations")).json()

    assert [conversation["id"] for conversation in alice_list] == [conversation_id]
    assert alice_list[0]["title"] == "新会话"
    assert bob_list == []


async def test_history_of_other_users_conversation_is_not_found(open_client: OpenClient) -> None:
    async with open_client(as_user="alice") as alice, open_client(as_user="bob") as bob:
        conversation_id = await create_conversation(alice)

        response = await bob.get(f"/api/conversations/{conversation_id}/messages")

    assert response.status_code == 404
