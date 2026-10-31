from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from ig_studio_mcp import server
from ig_studio_mcp.auth import TokenStore
from ig_studio_mcp.client import CONTAINER_POLL_SCHEDULE, IGClient, caption_problems, image_url_problems
from ig_studio_mcp.errors import ConfigError, MetaAPIError

from .conftest import USER_ID

IMG = "https://cdn.example.com/photo.jpg"
IMG2 = "https://cdn.example.com/photo2.jpg"


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


@pytest.fixture
async def client(store: TokenStore) -> AsyncIterator[IGClient]:
    async with IGClient(store, httpx.AsyncClient()) as c:
        c.sleeps = []  # type: ignore[attr-defined]

        async def fake_sleep(seconds: float) -> None:
            c.sleeps.append(seconds)  # type: ignore[attr-defined]

        c._sleep = fake_sleep
        yield c


@pytest.fixture
async def wired(client: IGClient) -> AsyncIterator[IGClient]:
    server.set_client(client)
    yield client
    server.set_client(None)


@pytest.fixture
def no_http() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def _mock_single_publish(api: respx.MockRouter, statuses: list[str] | None = None) -> dict[str, respx.Route]:
    statuses = statuses or ["FINISHED"]
    return {
        "create": api.post(f"/{USER_ID}/media").respond(json={"id": "CONT1"}),
        "status": api.get("/CONT1", params={"fields": "status_code,status"}).mock(
            side_effect=[httpx.Response(200, json={"id": "CONT1", "status_code": s}) for s in statuses]
        ),
        "publish": api.post(f"/{USER_ID}/media_publish").respond(json={"id": "MEDIA1"}),
        "permalink": api.get("/MEDIA1").respond(json={"id": "MEDIA1", "permalink": "https://instagram.com/p/abc"}),
    }


# ---------------------------------------------------------------- local validation


def test_caption_problems() -> None:
    assert caption_problems(None) == [] and caption_problems("Sunset ride #moto @friend") == []
    assert "2200" in caption_problems("x" * 2201)[0]
    assert "31 hashtags" in caption_problems(" ".join(f"#t{i}" for i in range(31)))[0]
    assert "21 @mentions" in caption_problems(" ".join(f"@u{i}" for i in range(21)))[0]
    assert caption_problems("mail me: a@b.com &#39; 30% off") == []


def test_image_url_problems() -> None:
    assert image_url_problems(IMG) == ([], [])
    assert image_url_problems("C:/photos/a.jpg")[0]
    assert image_url_problems("ftp://x.com/a.jpg")[0]
    assert "JPEG" in image_url_problems("https://x.com/a.png")[1][0]


# ---------------------------------------------------------------- publish_image


async def test_publish_image_flow(client: IGClient, api: respx.MockRouter) -> None:
    routes = _mock_single_publish(api)
    result = await client.publish_image(IMG, "Morning ride #moto")
    assert _form(routes["create"].calls.last.request) | {"access_token": "x"} == {
        "image_url": IMG, "caption": "Morning ride #moto", "access_token": "x",
    }
    assert _form(routes["publish"].calls.last.request)["creation_id"] == "CONT1"
    assert result.executed and result.media_id == "MEDIA1" and result.container_ids == ["CONT1"]
    assert result.permalink == "https://instagram.com/p/abc"
    assert client.sleeps == []  # type: ignore[attr-defined]  # FINISHED on the first check


async def test_publish_waits_while_in_progress(client: IGClient, api: respx.MockRouter) -> None:
    routes = _mock_single_publish(api, ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    await client.publish_image(IMG)
    assert routes["status"].call_count == 3
    assert client.sleeps == list(CONTAINER_POLL_SCHEDULE[1:3])  # type: ignore[attr-defined]
    assert routes["publish"].called


async def test_publish_gives_up_after_five_minutes(client: IGClient, api: respx.MockRouter) -> None:
    routes = _mock_single_publish(api, ["IN_PROGRESS"] * len(CONTAINER_POLL_SCHEDULE))
    with pytest.raises(MetaAPIError, match="still processing"):
        await client.publish_image(IMG)
    assert sum(client.sleeps) <= 300  # type: ignore[attr-defined]
    assert not routes["publish"].called


@pytest.mark.parametrize(
    ("status", "match"),
    [
        ({"status_code": "ERROR", "status": "Error: Media download has failed (2207052)"}, "couldn't download"),
        ({"status_code": "ERROR", "status": "Something odd"}, "Something odd"),
        ({"status_code": "EXPIRED"}, "expired"),
        ({"status_code": "PUBLISHED"}, "already published"),
    ],
)
async def test_container_failure_states(client: IGClient, api: respx.MockRouter, status: dict, match: str) -> None:
    api.post(f"/{USER_ID}/media").respond(json={"id": "CONT1"})
    api.get("/CONT1").respond(json={"id": "CONT1", **status})
    publish = api.post(f"/{USER_ID}/media_publish").respond(json={"id": "MEDIA1"})
    with pytest.raises(MetaAPIError, match=match):
        await client.publish_image(IMG)
    assert not publish.called


async def test_publish_reports_config_issue(client: IGClient, api: respx.MockRouter) -> None:
    routes = _mock_single_publish(api)
    routes["publish"].respond(json={"id": "MEDIA1", "config_issue": "CAPTION_NOT_ATTACHED"})
    result = await client.publish_image(IMG, "hi")
    assert any("CAPTION_NOT_ATTACHED" in n for n in result.notes)


async def test_permalink_failure_does_not_fail_publish(client: IGClient, api: respx.MockRouter) -> None:
    routes = _mock_single_publish(api)
    routes["permalink"].mock(return_value=httpx.Response(500, json={"error": {"message": "x", "code": 2}}))
    result = await client.publish_image(IMG)
    assert result.media_id == "MEDIA1" and result.permalink is None


async def test_image_fetch_error_is_actionable(client: IGClient, api: respx.MockRouter) -> None:
    api.post(f"/{USER_ID}/media").mock(return_value=httpx.Response(400, json={"error": {
        "message": "The media could not be fetched from this uri", "code": 9004, "error_subcode": 2207052,
    }}))
    with pytest.raises(MetaAPIError, match="must be public"):
        await client.publish_image(IMG)


async def test_daily_limit_error(client: IGClient, api: respx.MockRouter) -> None:
    routes = _mock_single_publish(api)
    routes["publish"].mock(return_value=httpx.Response(400, json={"error": {
        "message": "You reached maximum number of posts", "code": 9, "error_subcode": 2207042,
    }}))
    with pytest.raises(MetaAPIError, match="Daily publishing limit"):
        await client.publish_image(IMG)


async def test_client_rejects_bad_caption_without_http(client: IGClient, no_http: respx.MockRouter) -> None:
    with pytest.raises(ConfigError, match="hashtags"):
        await client.publish_image(IMG, " ".join(f"#t{i}" for i in range(31)))
    assert no_http.calls.call_count == 0


# ---------------------------------------------------------------- publish_carousel


async def test_publish_carousel_flow(client: IGClient, api: respx.MockRouter) -> None:
    created: list[dict[str, str]] = []

    def create(request: httpx.Request) -> httpx.Response:
        form = _form(request)
        created.append(form)
        cid = "PARENT" if form.get("media_type") == "CAROUSEL" else f"CHILD{len(created)}"
        return httpx.Response(200, json={"id": cid})

    api.post(f"/{USER_ID}/media").mock(side_effect=create)
    api.get(path__regex=r"/(CHILD\d|PARENT)$").respond(json={"status_code": "FINISHED"})
    publish = api.post(f"/{USER_ID}/media_publish").respond(json={"id": "MEDIA9"})
    api.get("/MEDIA9").respond(json={"permalink": "https://instagram.com/p/xyz"})

    result = await client.publish_carousel([IMG, IMG2], "Two shots")
    children, parent = created[:2], created[2]
    assert all(c["is_carousel_item"] == "true" and "caption" not in c for c in children)
    assert {c["image_url"] for c in children} == {IMG, IMG2}
    assert parent["media_type"] == "CAROUSEL" and parent["caption"] == "Two shots"
    assert parent["children"] == "CHILD1,CHILD2"
    assert _form(publish.calls.last.request)["creation_id"] == "PARENT"
    assert result.container_ids == ["CHILD1", "CHILD2", "PARENT"] and result.media_id == "MEDIA9"


@pytest.mark.parametrize("count", [1, 11])
async def test_carousel_size_checked_without_http(client: IGClient, no_http: respx.MockRouter, count: int) -> None:
    with pytest.raises(ConfigError, match="2-10 images"):
        await client.publish_carousel([IMG] * count)
    assert no_http.calls.call_count == 0


# ---------------------------------------------------------------- publishing limit


async def test_get_publishing_limit(client: IGClient, api: respx.MockRouter) -> None:
    route = api.get(f"/{USER_ID}/content_publishing_limit").respond(
        json={"data": [{"quota_usage": 2, "config": {"quota_total": 50, "quota_duration": 86400}}]}
    )
    limit = await client.get_publishing_limit()
    assert (limit.quota_usage, limit.quota_total, limit.quota_duration_hours, limit.remaining) == (2, 50, 24, 48)
    assert route.calls.last.request.url.params["fields"] == "config,quota_usage"


# ---------------------------------------------------------------- MCP tools


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("publish_image", {"image_url": IMG, "caption": "  Morning ride  "}),
        ("publish_carousel", {"image_urls": [IMG, IMG2], "caption": "Two shots"}),
    ],
)
async def test_publish_preview_makes_no_http_calls(no_http: respx.MockRouter, tool: str, args: dict) -> None:
    server.set_client(None)
    preview = (await server.mcp.call_tool(tool, args)).structured_content
    assert preview["executed"] is False and preview["container_ids"] == []
    assert "confirm=true" in preview["notes"][0]
    assert no_http.calls.call_count == 0
    assert server._client is None


async def test_preview_shows_trimmed_caption_and_warnings(no_http: respx.MockRouter) -> None:
    preview = (await server.mcp.call_tool(
        "publish_image", {"image_url": "https://x.com/a.png", "caption": "  hi  "}
    )).structured_content
    assert preview["caption"] == "hi"
    assert any("JPEG" in n for n in preview["notes"])


async def test_carousel_preview_flags_duplicates(no_http: respx.MockRouter) -> None:
    preview = (await server.mcp.call_tool("publish_carousel", {"image_urls": [IMG, IMG]})).structured_content
    assert any("repeated" in n for n in preview["notes"])


@pytest.mark.parametrize(
    ("tool", "args", "match"),
    [
        ("publish_image", {"image_url": "not a url"}, "http"),
        ("publish_image", {"image_url": IMG, "caption": "x" * 2201}, "2200"),
        ("publish_carousel", {"image_urls": [IMG]}, "2-10 images"),
    ],
)
async def test_invalid_posts_rejected_without_http(
    no_http: respx.MockRouter, tool: str, args: dict, match: str
) -> None:
    for confirm in (False, True):
        with pytest.raises(ToolError, match=match):
            await server.mcp.call_tool(tool, {**args, "confirm": confirm})
    assert no_http.calls.call_count == 0


async def test_publish_image_tool_confirmed(api: respx.MockRouter, wired: IGClient) -> None:
    _mock_single_publish(api)
    result = (await server.mcp.call_tool(
        "publish_image", {"image_url": IMG, "caption": "Go", "confirm": True}
    )).structured_content
    assert result["executed"] is True and result["permalink"] == "https://instagram.com/p/abc"


async def test_get_publishing_limit_tool(api: respx.MockRouter, wired: IGClient) -> None:
    api.get(f"/{USER_ID}/content_publishing_limit").respond(
        json={"data": [{"quota_usage": 0, "config": {"quota_total": 100, "quota_duration": 86400}}]}
    )
    result = (await server.mcp.call_tool("get_publishing_limit", {})).structured_content
    assert result["remaining"] == 100
