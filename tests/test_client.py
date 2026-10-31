from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from dotenv import dotenv_values

from ig_studio_mcp.auth import TokenStore
from ig_studio_mcp.client import GRAPH_API_VERSION, MEDIA_METRICS, IGClient
from ig_studio_mcp.errors import ConfigError, MetaAPIError, NetworkError

from .conftest import TOKEN, USER_ID, write_env


def _err(code: int, message: str = "err", subcode: int | None = None, status: int = 400) -> httpx.Response:
    error = {"message": message, "type": "OAuthException", "code": code}
    if subcode:
        error["error_subcode"] = subcode
    return httpx.Response(status, json={"error": error})


def _insight(name: str, value: int | float) -> dict:
    return {"name": name, "period": "lifetime", "values": [{"value": value}], "title": name, "id": f"x/{name}"}


def _expiring(days_left: float) -> dict:
    expires = datetime.now(UTC) + timedelta(days=days_left)
    return {"data": {"is_valid": True, "type": "USER", "expires_at": int(expires.timestamp()), "scopes": []}}


# ---------------------------------------------------------------- account discovery & profile


async def test_get_profile(client: IGClient, api: respx.MockRouter) -> None:
    route = api.get(f"/{USER_ID}").respond(
        json={"id": USER_ID, "username": "studio", "name": "Studio", "biography": "hello",
              "followers_count": 1200, "follows_count": 80, "media_count": 42,
              "profile_picture_url": "https://cdn/x.jpg"}
    )
    profile = await client.get_profile()
    assert profile.username == "studio" and profile.followers_count == 1200
    request = route.calls.last.request
    assert request.url.host == "graph.facebook.com"
    assert request.url.path == f"/{GRAPH_API_VERSION}/{USER_ID}"
    assert request.url.params["access_token"] == TOKEN
    assert "appsecret_proof" not in request.url.params
    assert "profile_picture_url" not in profile.model_dump()


async def test_account_discovered_once(client: IGClient, api: respx.MockRouter) -> None:
    media = api.get(f"/{USER_ID}/media").respond(json={"data": []})
    api.get(f"/{USER_ID}/insights").respond(json={"data": []})
    await client.list_recent_media()
    await client.list_recent_media()
    await client.get_account_insights("day")
    assert api.routes["assigned"].call_count == 1
    assert media.call_count == 2


async def test_ig_user_id_override_skips_discovery(tmp_path: Path, api: respx.MockRouter) -> None:
    env = tmp_path / ".env"
    write_env(env, IG_USER_ID="999")
    route = api.get("/999").respond(json={"id": "999", "username": "other"})
    async with IGClient(TokenStore(env), httpx.AsyncClient()) as c:
        assert (await c.get_profile()).username == "other"
    assert route.called and not api.routes["assigned"].called


async def test_falls_back_to_pages(client: IGClient, api: respx.MockRouter) -> None:
    api.routes["assigned"].mock(return_value=_err(100, "(#100) Tried accessing nonexisting field"))
    pages = api.get("/me/accounts").respond(json={"data": [
        {"id": "P1", "name": "My Page", "instagram_business_account": {"id": USER_ID, "username": "studio"}},
        {"id": "P2", "name": "No IG"},
    ]})
    api.get(f"/{USER_ID}").respond(json={"id": USER_ID, "username": "studio"})
    await client.get_profile()
    assert "instagram_business_account" in pages.calls.last.request.url.params["fields"]
    accounts = await client.list_instagram_accounts()
    assert [(a.id, a.page_name) for a in accounts] == [(USER_ID, "My Page")]


async def test_several_accounts_needs_ig_user_id(client: IGClient, api: respx.MockRouter) -> None:
    api.routes["assigned"].respond(json={"data": [{"id": "1", "username": "a"}, {"id": "2", "username": "b"}]})
    with pytest.raises(ConfigError, match=r"@a \(1\), @b \(2\).*IG_USER_ID"):
        await client.get_profile()


async def test_no_accounts(client: IGClient, api: respx.MockRouter) -> None:
    api.routes["assigned"].respond(json={"data": []})
    api.get("/me/accounts").respond(json={"data": []})
    with pytest.raises(ConfigError, match="Assign assets"):
        await client.get_profile()


async def test_appsecret_proof_sent_when_secret_set(tmp_path: Path, api: respx.MockRouter) -> None:
    env = tmp_path / ".env"
    write_env(env, IG_APP_SECRET="shh")
    route = api.get(f"/{USER_ID}").respond(json={"id": USER_ID, "username": "studio"})
    async with IGClient(TokenStore(env), httpx.AsyncClient()) as c:
        await c.get_profile()
    assert len(route.calls.last.request.url.params["appsecret_proof"]) == 64


# ---------------------------------------------------------------- media


async def test_list_recent_media_paginates(client: IGClient, api: respx.MockRouter) -> None:
    def media(i: int) -> dict:
        return {"id": str(i), "caption": f"post {i}", "media_type": "IMAGE", "media_product_type": "FEED",
                "timestamp": "2026-10-30T10:00:00+0000", "like_count": i, "comments_count": 0,
                "permalink": f"https://instagram.com/p/{i}"}

    route = api.get(f"/{USER_ID}/media").mock(
        side_effect=[
            httpx.Response(200, json={"data": [media(1), media(2)],
                                      "paging": {"cursors": {"after": "C1"}, "next": "https://next"}}),
            httpx.Response(200, json={"data": [media(3)],
                                      "paging": {"cursors": {"after": "C2"}, "next": "https://next2"}}),
        ]
    )
    result = await client.list_recent_media(limit=3)
    assert [m.id for m in result.items] == ["1", "2", "3"]
    assert result.count == 3 and result.has_more is True
    second = route.calls[1].request.url.params
    assert second["after"] == "C1" and second["limit"] == "1"


async def test_list_recent_media_last_page(client: IGClient, api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}/media").respond(json={"data": [{"id": "1"}], "paging": {"cursors": {"after": "C"}}})
    result = await client.list_recent_media(limit=10)
    assert result.count == 1 and result.has_more is False


async def test_list_recent_media_clamps_limit(client: IGClient, api: respx.MockRouter) -> None:
    route = api.get(f"/{USER_ID}/media").respond(json={"data": []})
    await client.list_recent_media(limit=10_000)
    assert route.calls.last.request.url.params["limit"] == "100"


# ---------------------------------------------------------------- comments


async def test_get_media_comments(client: IGClient, api: respx.MockRouter) -> None:
    route = api.get("/M1/comments").respond(
        json={
            "data": [
                {"id": "c1", "username": "alice", "text": "love it", "timestamp": "2026-10-30T10:00:00+0000",
                 "like_count": 2, "hidden": False,
                 "replies": {"data": [{"id": "r1"}, {"id": "r2"}]}},
                {"id": "c2", "from": {"id": "9", "username": "bob"}, "text": "nice",
                 "replies": {"data": [{"id": "r3"}], "paging": {"next": "https://more"}}},
                {"id": "c3", "username": "carol", "text": "no replies"},
            ]
        }
    )
    result = await client.get_media_comments("M1", limit=50)
    c1, c2, c3 = result.comments
    assert (c1.username, c1.reply_count, c1.reply_count_is_partial) == ("alice", 2, False)
    assert (c2.username, c2.reply_count, c2.reply_count_is_partial) == ("bob", 1, True)
    assert c3.reply_count == 0
    assert result.has_more is False
    assert "replies{id}" in route.calls.last.request.url.params["fields"]


async def test_comments_page_size_capped_at_50(client: IGClient, api: respx.MockRouter) -> None:
    route = api.get("/M1/comments").respond(json={"data": []})
    await client.get_media_comments("M1", limit=200)
    assert route.calls.last.request.url.params["limit"] == "50"


async def test_comments_unknown_media(client: IGClient, api: respx.MockRouter) -> None:
    api.get("/BAD/comments").mock(return_value=_err(100, "Unsupported get request.", subcode=33))
    with pytest.raises(MetaAPIError, match="not found"):
        await client.get_media_comments("BAD")


# ---------------------------------------------------------------- media insights


async def test_media_insights_reel(client: IGClient, api: respx.MockRouter) -> None:
    api.get("/R1").respond(json={"id": "R1", "media_type": "VIDEO", "media_product_type": "REELS"})
    route = api.get("/R1/insights").respond(
        json={"data": [_insight("reach", 500), _insight("saved", 12), _insight("shares", 3),
                       _insight("ig_reels_avg_watch_time", 4100)]}
    )
    result = await client.get_media_insights("R1")
    assert route.calls.last.request.url.params["metric"] == ",".join(MEDIA_METRICS["REELS"])
    assert result.metrics["saves"] == 12 and "saved" not in result.metrics
    assert result.metrics["reach"] == 500
    assert "likes" in result.unavailable and "saved" not in result.unavailable
    assert result.media_product_type == "REELS"


async def test_media_insights_infers_product_type(client: IGClient, api: respx.MockRouter) -> None:
    api.get("/P1").respond(json={"id": "P1", "media_type": "CAROUSEL_ALBUM"})
    route = api.get("/P1/insights").respond(json={"data": [_insight("reach", 10)]})
    result = await client.get_media_insights("P1")
    assert result.media_product_type == "FEED"
    assert "profile_visits" in route.calls.last.request.url.params["metric"]
    assert any("assumed FEED" in n for n in result.notes)


async def test_media_insights_falls_back_per_metric(client: IGClient, api: respx.MockRouter) -> None:
    api.get("/P2").respond(json={"id": "P2", "media_type": "IMAGE", "media_product_type": "FEED"})

    def handler(request: httpx.Request) -> httpx.Response:
        metric = request.url.params["metric"]
        if "," in metric or metric in {"follows", "profile_visits"}:
            return _err(100, "(#100) metric[0] must be one of the following values: ...")
        return httpx.Response(200, json={"data": [_insight(metric, 7)]})

    api.get("/P2/insights").mock(side_effect=handler)
    result = await client.get_media_insights("P2")
    assert result.metrics["reach"] == 7 and result.metrics["saves"] == 7
    assert set(result.unavailable) == {"follows", "profile_visits"}


async def test_media_insights_story_not_enough_viewers(client: IGClient, api: respx.MockRouter) -> None:
    api.get("/S1").respond(json={"id": "S1", "media_type": "IMAGE", "media_product_type": "STORY"})
    api.get("/S1/insights").mock(return_value=_err(10, "(#10) Not enough viewers for the media to show insights"))
    with pytest.raises(MetaAPIError, match="at least 5"):
        await client.get_media_insights("S1")


# ---------------------------------------------------------------- account insights


@pytest.mark.parametrize(("period", "days"), [("day", 1), ("week", 7), ("days_28", 28)])
async def test_account_insights_window(client: IGClient, api: respx.MockRouter, period: str, days: int) -> None:
    follows = api.get(f"/{USER_ID}/insights", params={"metric": "follows_and_unfollows"}).respond(
        json={"data": [{"name": "follows_and_unfollows", "period": "day", "total_value": {"breakdowns": [
            {"dimension_keys": ["follow_type"], "results": [
                {"dimension_values": ["FOLLOWER"], "value": 3},
                {"dimension_values": ["NON_FOLLOWER"], "value": 1},
            ]},
        ]}}]}
    )
    route = api.get(f"/{USER_ID}/insights").respond(
        json={"data": [
            {"name": "reach", "period": "day", "total_value": {"value": 900}},
            {"name": "saves", "period": "day", "total_value": {"value": 15}},
        ]}
    )
    now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    result = await client.get_account_insights(period, now=now)  # type: ignore[arg-type]
    params = route.calls.last.request.url.params
    assert params["period"] == "day" and params["metric_type"] == "total_value"
    assert "follows_and_unfollows" not in params["metric"]
    assert int(params["until"]) - int(params["since"]) == days * 86400
    assert follows.calls.last.request.url.params["breakdown"] == "follow_type"
    assert result.metrics == {"reach": 900, "saves": 15, "follows": 3, "unfollows": 1}
    assert result.until == "2026-10-31T12:00:00Z"
    assert "views" in result.unavailable and "follows_and_unfollows" not in result.unavailable


async def test_account_insights_no_follow_activity_is_zero(client: IGClient, api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}/insights", params={"metric": "follows_and_unfollows"}).respond(
        json={"data": [{"name": "follows_and_unfollows", "period": "day"}]}
    )
    api.get(f"/{USER_ID}/insights").respond(json={"data": []})
    result = await client.get_account_insights("day")
    assert (result.metrics["follows"], result.metrics["unfollows"]) == (0, 0)
    assert "follows_and_unfollows" not in result.unavailable


async def test_account_insights_follows_unavailable(client: IGClient, api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}/insights", params={"metric": "follows_and_unfollows"}).mock(
        return_value=_err(100, "(#100) Not enough followers")
    )
    api.get(f"/{USER_ID}/insights").respond(json={"data": [{"name": "reach", "total_value": {"value": 5}}]})
    result = await client.get_account_insights("week")
    assert result.metrics == {"reach": 5}
    assert "follows_and_unfollows" in result.unavailable


async def test_account_insights_rejects_bad_period(client: IGClient) -> None:
    with pytest.raises(ConfigError, match="period"):
        await client.get_account_insights("month")  # type: ignore[arg-type]


# ---------------------------------------------------------------- errors & token safety


async def test_expired_token_error(client: IGClient, api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}").mock(return_value=_err(190, "Error validating access token", subcode=463, status=401))
    with pytest.raises(MetaAPIError) as info:
        await client.get_profile()
    assert info.value.code == 190 and "expired" in str(info.value).lower()
    assert TOKEN not in str(info.value)


async def test_rate_limit_error(client: IGClient, api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}/media").mock(return_value=httpx.Response(
        400,
        json={"error": {"message": "Application request limit reached", "code": 4}},
        headers={"X-Business-Use-Case-Usage": '{"1": [{"estimated_time_to_regain_access": 7}]}'},
    ))
    with pytest.raises(MetaAPIError, match="retry after 7 minute"):
        await client.list_recent_media()


async def test_error_in_200_body(client: IGClient, api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}").respond(200, json={"error": {"message": "weird", "code": 2}})
    with pytest.raises(MetaAPIError, match="Temporary"):
        await client.get_profile()


async def test_network_error_does_not_leak_token(client: IGClient, api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}").mock(side_effect=httpx.ConnectError(f"failed for access_token={TOKEN}"))
    with pytest.raises(NetworkError) as info:
        await client.get_profile()
    assert TOKEN not in str(info.value)


async def test_timeout(client: IGClient, api: respx.MockRouter) -> None:
    api.get(f"/{USER_ID}").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(NetworkError, match="Timed out"):
        await client.get_profile()


async def test_token_never_logged(client: IGClient, api: respx.MockRouter, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    api.get(f"/{USER_ID}").respond(json={"id": USER_ID, "username": "studio"})
    await client.get_profile()
    assert TOKEN not in caplog.text


# ---------------------------------------------------------------- token lifecycle


async def test_token_status(client: IGClient, api: respx.MockRouter) -> None:
    status = await client.token_status()
    assert status.type == "SYSTEM_USER" and status.expires_at is None
    params = api.routes["debug_token"].calls.last.request.url.params
    assert params["input_token"] == TOKEN


async def test_never_expiring_token_checked_once_and_not_refreshed(client: IGClient, api: respx.MockRouter) -> None:
    exchange = api.get("/oauth/access_token").respond(json={"access_token": "EAAnew"})
    api.get(f"/{USER_ID}").respond(json={"id": USER_ID, "username": "studio"})
    await client.get_profile()
    await client.get_profile()
    assert api.routes["debug_token"].call_count == 1
    assert not exchange.called


async def test_expiring_user_token_renewed(tmp_path: Path, api: respx.MockRouter) -> None:
    env = tmp_path / ".env"
    write_env(env, IG_APP_ID="app1", IG_APP_SECRET="shh")
    api.routes["debug_token"].respond(json=_expiring(3))
    exchange = api.get("/oauth/access_token").respond(
        json={"access_token": "EAAnew-token", "token_type": "bearer", "expires_in": 5183944}
    )
    profile = api.get(f"/{USER_ID}").respond(json={"id": USER_ID, "username": "studio"})
    async with IGClient(TokenStore(env), httpx.AsyncClient()) as c:
        await c.get_profile()
        await c.get_profile()  # still "expiring" per the mock: must not re-exchange again
    assert exchange.call_count == 1
    params = exchange.calls.last.request.url.params
    assert params["grant_type"] == "fb_exchange_token"
    assert (params["client_id"], params["client_secret"], params["fb_exchange_token"]) == ("app1", "shh", TOKEN)
    assert "access_token" not in params
    assert profile.calls.last.request.url.params["access_token"] == "EAAnew-token"
    assert dotenv_values(env)["IG_ACCESS_TOKEN"] == "EAAnew-token"


async def test_expiring_token_without_app_credentials_keeps_working(client: IGClient, api: respx.MockRouter) -> None:
    api.routes["debug_token"].respond(json=_expiring(3))
    exchange = api.get("/oauth/access_token").respond(json={"access_token": "EAAnew"})
    profile = api.get(f"/{USER_ID}").respond(json={"id": USER_ID, "username": "studio"})
    await client.get_profile()
    await client.get_profile()
    assert not exchange.called
    assert profile.calls.last.request.url.params["access_token"] == TOKEN
    assert api.routes["debug_token"].call_count == 1


async def test_debug_token_failure_does_not_block_calls(client: IGClient, api: respx.MockRouter) -> None:
    api.routes["debug_token"].mock(return_value=_err(100, "(#100) You must provide an app access token"))
    api.get(f"/{USER_ID}").respond(json={"id": USER_ID, "username": "studio"})
    assert (await client.get_profile()).username == "studio"


async def test_exchange_short_lived_token(tmp_path: Path, api: respx.MockRouter) -> None:
    env = tmp_path / ".env"
    env.write_text("IG_APP_ID=app1\nIG_APP_SECRET=shh\n")
    route = api.get("/oauth/access_token").respond(json={"access_token": "EAAlong", "expires_in": 5183944})
    async with IGClient(TokenStore(env), httpx.AsyncClient()) as c:
        info = await c.exchange_short_lived_token("EAAshort")
    assert info.expires_in_days == 59
    assert route.calls.last.request.url.params["fb_exchange_token"] == "EAAshort"
    assert dotenv_values(env)["IG_ACCESS_TOKEN"] == "EAAlong"
    assert "EAAlong" not in info.model_dump_json()


async def test_exchange_requires_app_credentials(client: IGClient) -> None:
    with pytest.raises(ConfigError, match="IG_APP_ID and IG_APP_SECRET"):
        await client.exchange_short_lived_token("EAAshort")


async def test_exchange_error_redacts_secret(tmp_path: Path, api: respx.MockRouter) -> None:
    env = tmp_path / ".env"
    write_env(env, IG_APP_ID="app1", IG_APP_SECRET="app-secret-xyz")
    api.get("/oauth/access_token").mock(return_value=_err(100, "bad client_secret=app-secret-xyz"))
    async with IGClient(TokenStore(env), httpx.AsyncClient(), auto_refresh=False) as c:
        with pytest.raises(MetaAPIError) as info:
            await c.exchange_short_lived_token("EAAshort")
    assert "app-secret-xyz" not in str(info.value)
