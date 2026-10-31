from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from ig_studio_mcp import server
from ig_studio_mcp.auth import TokenStore
from ig_studio_mcp.client import API_URL, GRAPH_API_VERSION, IGClient

from .conftest import TOKEN

WRITE_CALLS = [
    ("reply_to_comment", {"comment_id": "C1", "message": "  Thanks for watching!  "}),
    ("hide_comment", {"comment_id": "C1"}),
    ("hide_comment", {"comment_id": "C1", "hide": False}),
    ("delete_comment", {"comment_id": "C1"}),
]


@pytest.fixture
def no_http() -> Iterator[respx.MockRouter]:
    """Any HTTP request at all fails the test."""
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def wired(store: TokenStore) -> AsyncIterator[IGClient]:
    client = IGClient(store, httpx.AsyncClient())
    server.set_client(client)
    yield client
    server.set_client(None)
    await client.aclose()


# ---------------------------------------------------------------- confirm=False never calls the API


@pytest.mark.parametrize(("tool", "args"), WRITE_CALLS)
async def test_preview_makes_no_http_calls(no_http: respx.MockRouter, tool: str, args: dict) -> None:
    server.set_client(None)
    result = await server.mcp.call_tool(tool, args)
    preview = result.structured_content
    assert preview["executed"] is False
    assert preview["comment_id"] == "C1"
    assert "confirm=true" in preview["notes"][0]
    assert no_http.calls.call_count == 0
    assert server._client is None  # the preview never even built an API client


@pytest.mark.parametrize(("tool", "args"), WRITE_CALLS)
async def test_confirm_defaults_to_false(no_http: respx.MockRouter, tool: str, args: dict) -> None:
    server.set_client(None)
    assert "confirm" not in args
    await server.mcp.call_tool(tool, args)
    assert no_http.calls.call_count == 0


async def test_reply_preview_shows_exact_text(no_http: respx.MockRouter) -> None:
    preview = (await server.mcp.call_tool("reply_to_comment", WRITE_CALLS[0][1])).structured_content
    assert preview["message"] == "Thanks for watching!"
    assert preview["request"] == f"POST /{GRAPH_API_VERSION}/C1/replies"


async def test_preview_requests_describe_the_call(no_http: respx.MockRouter) -> None:
    hide = (await server.mcp.call_tool("hide_comment", {"comment_id": "C1"})).structured_content
    unhide = (await server.mcp.call_tool("hide_comment", {"comment_id": "C1", "hide": False})).structured_content
    delete = (await server.mcp.call_tool("delete_comment", {"comment_id": "C1"})).structured_content
    assert hide["request"].endswith("/C1?hide=true") and hide["action"] == "hide_comment"
    assert unhide["request"].endswith("/C1?hide=false") and unhide["action"] == "unhide_comment"
    assert delete["request"] == f"DELETE /{GRAPH_API_VERSION}/C1" and "cannot be undone" in delete["summary"]


@pytest.mark.parametrize("confirm", [False, True])
async def test_empty_message_rejected_without_http(no_http: respx.MockRouter, confirm: bool) -> None:
    with pytest.raises(ToolError, match="message is empty"):
        await server.mcp.call_tool("reply_to_comment", {"comment_id": "C1", "message": "   ", "confirm": confirm})
    assert no_http.calls.call_count == 0


@pytest.mark.parametrize("tool", ["hide_comment", "delete_comment"])
async def test_blank_comment_id_rejected_without_http(no_http: respx.MockRouter, tool: str) -> None:
    with pytest.raises(ToolError, match="comment_id is required"):
        await server.mcp.call_tool(tool, {"comment_id": " ", "confirm": True})
    assert no_http.calls.call_count == 0


# ---------------------------------------------------------------- confirm=True performs the call


async def test_reply_confirmed(api: respx.MockRouter, wired: IGClient) -> None:
    route = api.post("/C1/replies").respond(json={"id": "R9"})
    result = (
        await server.mcp.call_tool("reply_to_comment", {"comment_id": "C1", "message": " Thanks! ", "confirm": True})
    ).structured_content
    assert result["executed"] is True and result["success"] is True and result["reply_id"] == "R9"
    request = route.calls.last.request
    body = parse_qs(request.content.decode())
    assert body["message"] == ["Thanks!"] and body["access_token"] == [TOKEN]
    assert "access_token" not in request.url.params  # token stays out of the URL for POSTs


@pytest.mark.parametrize(("hide", "expected"), [(True, "true"), (False, "false")])
async def test_hide_confirmed(api: respx.MockRouter, wired: IGClient, hide: bool, expected: str) -> None:
    route = api.post("/C1").respond(json={"success": True})
    result = (
        await server.mcp.call_tool("hide_comment", {"comment_id": "C1", "hide": hide, "confirm": True})
    ).structured_content
    assert result["executed"] is True and result["success"] is True
    assert parse_qs(route.calls.last.request.content.decode())["hide"] == [expected]


async def test_delete_confirmed(api: respx.MockRouter, wired: IGClient) -> None:
    route = api.delete("/C1").respond(json={"success": True})
    result = (await server.mcp.call_tool("delete_comment", {"comment_id": "C1", "confirm": True})).structured_content
    assert result["executed"] is True and result["success"] is True
    assert route.calls.last.request.url.params["access_token"] == TOKEN


async def test_reply_to_hidden_comment_error_is_actionable(api: respx.MockRouter, wired: IGClient) -> None:
    api.post("/C1/replies").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": f"Cannot reply to hidden comment (token {TOKEN})", "code": 100}}
        )
    )
    with pytest.raises(ToolError) as info:
        await server.mcp.call_tool("reply_to_comment", {"comment_id": "C1", "message": "hi", "confirm": True})
    assert "hidden comment" in str(info.value)
    assert TOKEN not in str(info.value)


async def test_write_tool_annotations() -> None:
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert tools["delete_comment"].annotations.destructive_hint is True
    assert tools["hide_comment"].annotations.destructive_hint is False
    for name in ("reply_to_comment", "hide_comment", "delete_comment"):
        assert tools[name].annotations.read_only_hint is False
        assert "confirm=false first" in tools[name].description


def test_api_url_is_facebook() -> None:
    assert API_URL.startswith("https://graph.facebook.com/")
