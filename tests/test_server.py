from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from ig_studio_mcp import server
from ig_studio_mcp.auth import TokenStore
from ig_studio_mcp.client import IGClient

from .conftest import TOKEN, USER_ID


@pytest.fixture(autouse=True)
async def _wire_client(store: TokenStore) -> AsyncIterator[None]:
    client = IGClient(store, httpx.AsyncClient())
    server.set_client(client)
    yield
    server.set_client(None)
    await client.aclose()


READ_TOOLS = {
    "get_profile", "list_recent_media", "get_media_comments", "get_media_insights", "get_account_insights",
    "get_publishing_limit",
}
WRITE_TOOLS = {"reply_to_comment", "hide_comment", "delete_comment", "publish_image", "publish_carousel"}


async def test_tools_registered() -> None:
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for tool in tools.values():
        assert tool.output_schema is not None
        assert len(tool.description or "") > 80
    for name in WRITE_TOOLS:
        params = tools[name].input_schema["properties"]
        assert params["confirm"]["default"] is False


async def test_read_tools_read_only() -> None:
    tools = {t.name: t for t in await server.mcp.list_tools() if t.name in READ_TOOLS}
    for tool in tools.values():
        assert tool.annotations.read_only_hint is True
        assert tool.output_schema is not None
        assert len(tool.description or "") > 80


async def test_get_profile_tool_returns_structured(api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}").respond(json={"id": USER_ID, "username": "studio", "followers_count": 5})
    result = await server.mcp.call_tool("get_profile", {})
    assert result.structured_content["username"] == "studio"
    assert result.structured_content["followers_count"] == 5


async def test_tool_error_is_actionable_and_redacted(api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}").mock(return_value=httpx.Response(
        401, json={"error": {"message": f"Invalid OAuth access token {TOKEN}", "code": 190}}
    ))
    with pytest.raises(ToolError) as info:
        await server.mcp.call_tool("get_profile", {})
    assert "Token setup" in str(info.value)
    assert TOKEN not in str(info.value)


async def test_account_insights_rejects_invalid_period() -> None:
    with pytest.raises(ToolError):
        await server.mcp.call_tool("get_account_insights", {"period": "month"})
