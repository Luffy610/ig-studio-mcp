from __future__ import annotations

import json

import httpx
import pytest

from ig_studio_mcp.errors import from_response, redact


def _error(code: int, subcode: int | None = None, message: str = "boom", headers: dict | None = None) -> httpx.Response:
    err = {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "abc"}
    if subcode:
        err["error_subcode"] = subcode
    return httpx.Response(400, json={"error": err}, headers=headers or {})


def test_expired_token_message() -> None:
    exc = from_response(_error(190, 463, "Error validating access token: Session has expired"))
    assert "expired" in str(exc).lower()
    assert "system-user token" in str(exc)
    assert exc.code == 190 and exc.subcode == 463


def test_invalid_token_message() -> None:
    assert "ig-studio-mcp check" in str(from_response(_error(190)))


def test_page_role_message() -> None:
    assert "Business settings" in str(from_response(_error(190, 492)))


@pytest.mark.parametrize("code", [4, 17, 32, 613, 80001, 80002])
def test_rate_limit_reads_retry_minutes(code: int) -> None:
    usage = {"1784": [{"type": "instagram", "call_count": 100, "estimated_time_to_regain_access": 12}]}
    exc = from_response(_error(code, headers={"x-business-use-case-usage": json.dumps(usage)}))
    assert exc.is_rate_limit
    assert exc.retry_after_minutes == 12
    assert "retry after 12 minute(s)" in str(exc)


def test_rate_limit_without_header() -> None:
    exc = from_response(_error(4))
    assert exc.retry_after_minutes is None
    assert "Rate limited" in str(exc)


def test_permission_error_lists_scopes() -> None:
    assert "instagram_manage_insights" in str(from_response(_error(10, message="Application does not have permission")))


def test_story_viewers_error() -> None:
    assert "at least 5" in str(
        from_response(_error(10, message="(#10) Not enough viewers for the media to show insights"))
    )


def test_not_found() -> None:
    exc = from_response(_error(100, 33, "Unsupported get request."))
    assert exc.is_invalid_parameter
    assert "not found" in str(exc).lower()


def test_non_json_body() -> None:
    exc = from_response(httpx.Response(502, text="<html>Bad gateway</html>"))
    assert "HTTP 502" in str(exc)


def test_secrets_are_redacted_from_meta_messages() -> None:
    exc = from_response(_error(100, message="bad token SECRET123 in access_token=SECRET123&x=1"), "SECRET123")
    assert "SECRET123" not in str(exc)
    assert "SECRET123" not in (exc.meta_message or "")


def test_redact_pattern() -> None:
    assert redact("GET /me?access_token=abc.def&fields=id") == "GET /me?access_token=***&fields=id"
    assert redact("client_secret=shh") == "client_secret=***"
