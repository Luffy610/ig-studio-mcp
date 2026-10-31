from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import respx

from ig_studio_mcp import server
from ig_studio_mcp.client import IGClient
from ig_studio_mcp.errors import ConfigError, MetaAPIError

from .conftest import USER_ID

INSIGHTS = f"/{USER_ID}/insights"


def _breakdown(metric: str, keys: list[str], rows: list[tuple[list[str], int]]) -> dict:
    return {
        "data": [
            {
                "name": metric,
                "period": "lifetime",
                "total_value": {
                    "breakdowns": [
                        {"dimension_keys": keys, "results": [{"dimension_values": d, "value": v} for d, v in rows]}
                    ]
                },
            }
        ]
    }


def _demographics_handler(request: httpx.Request) -> httpx.Response:
    metric = request.url.params["metric"]
    data = {
        "age": [(["18-24"], 30), (["25-34"], 60), (["13-17"], 10)],
        "gender": [(["F"], 25), (["M"], 70), (["U"], 5)],
        "country": [(["IN"], 90), (["US"], 10)],
        "city": [(["Mumbai, Maharashtra"], 40), (["Pune, Maharashtra"], 20)],
        "age,gender": [(["25-34", "M"], 45), (["18-24", "F"], 15)],
    }[request.url.params["breakdown"]]
    return httpx.Response(200, json=_breakdown(metric, request.url.params["breakdown"].split(","), data))


@pytest.fixture
async def wired(client: IGClient) -> AsyncIterator[IGClient]:
    server.set_client(client)
    yield client
    server.set_client(None)


# ---------------------------------------------------------------- demographics


async def test_all_breakdowns(client: IGClient, api: respx.MockRouter) -> None:
    route = api.get(INSIGHTS).mock(side_effect=_demographics_handler)
    result = await client.get_audience_demographics()
    assert route.call_count == 4
    params = route.calls.last.request.url.params
    assert params["metric"] == "follower_demographics" and params["period"] == "lifetime"
    assert params["metric_type"] == "total_value" and params["timeframe"] == "this_month"
    assert {c.request.url.params["breakdown"] for c in route.calls} == {"age", "gender", "country", "city"}
    assert [b.label for b in result.breakdowns["age"]] == ["25-34", "18-24", "13-17"]
    assert result.breakdowns["age"][0].percent == 60.0
    assert [b.label for b in result.breakdowns["gender"]] == ["male", "female", "unknown"]
    assert result.breakdowns["country"][0].label == "IN"
    assert result.unavailable == []
    assert any("top 45 cities" in n for n in result.notes)


@pytest.mark.parametrize(
    ("audience", "metric"),
    [("engaged", "engaged_audience_demographics"), ("reached", "reached_audience_demographics")],
)
async def test_engaged_and_reached(client: IGClient, api: respx.MockRouter, audience: str, metric: str) -> None:
    route = api.get(INSIGHTS).mock(side_effect=_demographics_handler)
    result = await client.get_audience_demographics(audience, "gender", "this_week")  # type: ignore[arg-type]
    params = route.calls.last.request.url.params
    assert (params["metric"], params["timeframe"]) == (metric, "this_week")
    assert result.audience == audience and list(result.breakdowns) == ["gender"]


async def test_age_gender_labels(client: IGClient, api: respx.MockRouter) -> None:
    api.get(INSIGHTS).mock(side_effect=_demographics_handler)
    result = await client.get_audience_demographics(breakdown="age_gender")
    assert [b.label for b in result.breakdowns["age_gender"]] == ["25-34, male", "18-24, female"]


async def test_partial_failure_is_unavailable(client: IGClient, api: respx.MockRouter) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["breakdown"] == "city":
            return httpx.Response(400, json={"error": {"message": "(#100) not enough data", "code": 100}})
        return _demographics_handler(request)

    api.get(INSIGHTS).mock(side_effect=handler)
    result = await client.get_audience_demographics()
    assert "city" in result.unavailable and "city" not in result.breakdowns
    assert set(result.breakdowns) == {"age", "gender", "country"}


async def test_all_failing_raises(client: IGClient, api: respx.MockRouter) -> None:
    api.get(INSIGHTS).respond(400, json={"error": {"message": "(#100) timeframe not supported", "code": 100}})
    with pytest.raises(MetaAPIError, match="timeframe not supported"):
        await client.get_audience_demographics()


async def test_empty_breakdown_is_unavailable(client: IGClient, api: respx.MockRouter) -> None:
    api.get(INSIGHTS).respond(json={"data": [{"name": "follower_demographics", "total_value": {}}]})
    result = await client.get_audience_demographics(breakdown="age")
    assert result.breakdowns == {} and result.unavailable == ["age"]


@pytest.mark.parametrize(
    "kwargs",
    [{"audience": "fans"}, {"breakdown": "income"}, {"timeframe": "last_30_days"}],
)
async def test_demographics_validation(client: IGClient, api: respx.MockRouter, kwargs: dict) -> None:
    route = api.get(INSIGHTS).respond(json={"data": []})
    with pytest.raises(ConfigError):
        await client.get_audience_demographics(**kwargs)
    assert not route.called


# ---------------------------------------------------------------- active hours


def _online(days: list[tuple[str, dict[str, int]]]) -> dict:
    return {
        "data": [
            {
                "name": "online_followers",
                "period": "lifetime",
                "values": [{"value": hours, "end_time": end} for end, hours in days],
            }
        ]
    }


ONLINE = _online(
    [
        ("2026-10-30T07:00:00+0000", {}),  # a day without data
        ("2026-10-31T07:00:00+0000", {"0": 100, "15": 20, "10": 120}),
        ("2026-11-01T07:00:00+0000", {"0": 80, "15": 40, "10": 100}),
    ]
)


async def test_active_hours_utc(client: IGClient, api: respx.MockRouter) -> None:
    route = api.get(INSIGHTS).respond(json=ONLINE)
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    result = await client.get_follower_active_hours(now=now)
    params = route.calls.last.request.url.params
    assert params["metric"] == "online_followers" and params["period"] == "lifetime"
    assert int(params["until"]) - int(params["since"]) == 30 * 86400
    assert result.days_with_data == 2
    # Pacific hour 0 of a day ending 07:00 UTC starts at 07:00 UTC.
    by_hour = {h.hour: h.avg_online_followers for h in result.hours}
    assert by_hour == {7: 90.0, 17: 110.0, 22: 30.0}
    assert result.best_hours == [17, 7, 22]


async def test_active_hours_local_timezone(client: IGClient, api: respx.MockRouter) -> None:
    api.get(INSIGHTS).respond(json=ONLINE)
    result = await client.get_follower_active_hours(timezone="Asia/Kolkata")
    by_hour = {h.hour: h.avg_online_followers for h in result.hours}
    # 07:00 UTC = 12:30 IST, 17:00 UTC = 22:30 IST, 22:00 UTC = 03:30 IST
    assert by_hour == {12: 90.0, 22: 110.0, 3: 30.0}
    assert result.timezone == "Asia/Kolkata" and result.best_hours[0] == 22


async def test_active_hours_clamps_days(client: IGClient, api: respx.MockRouter) -> None:
    route = api.get(INSIGHTS).respond(json=_online([]))
    result = await client.get_follower_active_hours(days=90)
    params = route.calls.last.request.url.params
    assert int(params["until"]) - int(params["since"]) == 30 * 86400
    assert result.days_requested == 30 and result.days_with_data == 0
    assert result.hours == [] and result.best_hours == []
    assert any("100+ followers" in n for n in result.notes)


async def test_active_hours_bad_timezone(client: IGClient, api: respx.MockRouter) -> None:
    route = api.get(INSIGHTS).respond(json=ONLINE)
    with pytest.raises(ConfigError, match="IANA"):
        await client.get_follower_active_hours(timezone="Mars/Olympus")
    assert not route.called


# ---------------------------------------------------------------- MCP tools


async def test_tools_return_structured(api: respx.MockRouter, wired: IGClient) -> None:
    api.get(INSIGHTS, params={"metric": "online_followers"}).respond(json=ONLINE)
    api.get(INSIGHTS).mock(side_effect=_demographics_handler)
    demo = (await server.mcp.call_tool("get_audience_demographics", {"breakdown": "gender"})).structured_content
    assert demo["breakdowns"]["gender"][0] == {"label": "male", "value": 70, "percent": 70.0}
    hours = (await server.mcp.call_tool("get_follower_active_hours", {"timezone": "UTC"})).structured_content
    assert hours["best_hours"] == [17, 7, 22]
