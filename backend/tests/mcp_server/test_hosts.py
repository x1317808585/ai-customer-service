"""MCP Server 只接受发往本机或 MCP_SERVER_URL 主机的请求（防 DNS 重绑定）。"""

from app.config import Settings
from app.mcp_token import issue_mcp_token
from app.models import User
from tests.mcp_server.conftest import OpenMcpHttp
from tests.mcp_server.test_access import INITIALIZE, MCP_HEADERS


async def test_only_the_configured_server_host_is_accepted(
    open_mcp_http: OpenMcpHttp, test_settings: Settings, users: dict[str, User]
) -> None:
    # Docker Compose 里后端用服务名访问 MCP
    settings = test_settings.model_copy(update={"mcp_server_url": "http://mcp:8001/mcp"})
    token = issue_mcp_token(users["alice"].id, settings)

    async with open_mcp_http(token, settings) as client:
        accepted = await client.post("http://mcp:8001/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        rejected = await client.post("http://evil.example:8001/mcp", json=INITIALIZE, headers=MCP_HEADERS)

    assert accepted.status_code == 200
    assert rejected.status_code == 421
