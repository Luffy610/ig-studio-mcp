"""Error types and Meta error-code -> plain-language mapping.

Every message produced here is meant to be shown to an LLM (and through it, the
user), so it says what went wrong *and* what to do next. Messages are always
passed through `redact()` so an access token can never leak into a tool result.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

_TOKEN_PATTERN = re.compile(r"(access_token|client_secret)=[^&\s\"']+", re.IGNORECASE)

TOKEN_HELP = "see README -> 'Token setup'"


def redact(text: str, *secrets: str | None) -> str:
    """Remove access tokens / secrets from arbitrary text."""
    text = _TOKEN_PATTERN.sub(r"\1=***", text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class IGStudioError(Exception):
    """Base error. `str(err)` is a user-facing, actionable message."""


class ConfigError(IGStudioError):
    """Missing or malformed local configuration (.env)."""


class NetworkError(IGStudioError):
    """graph.facebook.com could not be reached."""


class MetaAPIError(IGStudioError):
    """An error response from the Instagram Graph API."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        error_type: str | None = None,
        meta_message: str | None = None,
        http_status: int | None = None,
        retry_after_minutes: int | None = None,
        fbtrace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.error_type = error_type
        self.meta_message = meta_message
        self.http_status = http_status
        self.retry_after_minutes = retry_after_minutes
        self.fbtrace_id = fbtrace_id

    @property
    def is_rate_limit(self) -> bool:
        return self.code in RATE_LIMIT_CODES

    @property
    def is_invalid_parameter(self) -> bool:
        return self.code == 100

    @property
    def is_permission(self) -> bool:
        return self.code in PERMISSION_CODES


# Graph API error codes (https://developers.facebook.com/docs/graph-api/guides/error-handling)
TOKEN_CODES = {190, 102}
RATE_LIMIT_CODES = {4, 17, 32, 613, 80001, 80002}
PERMISSION_CODES = {10, *range(200, 300)}
TRANSIENT_CODES = {1, 2}

# Content-publishing error subcodes (Instagram Platform error-codes reference).
PUBLISH_SUBCODE_HINTS = {
    2207001: "Instagram server error while publishing - retry in a few minutes.",
    2207003: "Instagram timed out downloading the image. Use a faster host or a smaller file.",
    2207004: "The image is larger than 8 MB.",
    2207005: "Unsupported image format - Instagram only accepts JPEG.",
    2207008: "The media container expired or doesn't exist. Publish again to create a new one.",
    2207009: "The image's aspect ratio is out of range; use between 4:5 (portrait) and 1.91:1 (landscape).",
    2207010: "The caption is too long (max 2,200 characters, 30 hashtags, 20 @mentions).",
    2207020: "The media container expired. Publish again to create a new one.",
    2207027: "The media isn't ready to publish yet. Wait a minute and try again.",
    2207028: "A carousel needs 2-10 images.",
    2207032: "Instagram failed to create the media - retry.",
    2207040: "Too many @mentions in the caption (max 20).",
    2207042: "Daily publishing limit reached. Check get_publishing_limit and try again tomorrow.",
    2207050: "The Instagram account is restricted and can't publish right now.",
    2207051: "Instagram blocked this action as suspected spam. Wait before posting again.",
    2207052: "Instagram couldn't download the image. The URL must be public (no login, no expiring link) and point directly to a JPEG.",
}


def container_error_message(status: object) -> str:
    """Explain a media container whose status_code is ERROR (`status` carries a subcode)."""
    text = str(status or "unknown error")
    for subcode, hint in PUBLISH_SUBCODE_HINTS.items():
        if str(subcode) in text:
            return f"Instagram could not process the media: {hint} (Meta says: {text})"
    return f"Instagram could not process the media (Meta says: {text}). Nothing was published."


def _retry_after_minutes(headers: httpx.Headers) -> int | None:
    """Read `estimated_time_to_regain_access` (minutes) from Meta's usage headers."""
    raw = headers.get("x-business-use-case-usage")
    if not raw:
        return None
    try:
        usage = json.loads(raw)
    except ValueError:
        return None
    minutes = [
        entry.get("estimated_time_to_regain_access", 0)
        for entries in usage.values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict)
    ]
    best = max(minutes, default=0)
    return int(best) if best else None


def friendly_message(
    code: int | None,
    subcode: int | None,
    meta_message: str,
    retry_after_minutes: int | None = None,
) -> str:
    """Turn a Meta error into an actionable sentence."""
    if subcode in PUBLISH_SUBCODE_HINTS:
        hint = PUBLISH_SUBCODE_HINTS[subcode]
    elif code in TOKEN_CODES:
        if subcode == 463:
            hint = (
                "Access token expired. Expired tokens can't be refreshed: generate a new one "
                f"({TOKEN_HELP}). A system-user token avoids expiry altogether."
            )
        elif subcode == 460:
            hint = f"Access token was invalidated (password change or security event). Generate a new token ({TOKEN_HELP})."
        elif subcode == 492:
            hint = "The token's user has no role on the Facebook Page linked to this Instagram account. Re-assign the asset in Business settings."
        else:
            hint = (
                "Access token is invalid or revoked. Generate a new token "
                f"({TOKEN_HELP}) and check it with `uv run ig-studio-mcp check`."
            )
    elif code in RATE_LIMIT_CODES:
        when = f"{retry_after_minutes} minute(s)" if retry_after_minutes else "a while (usually under an hour)"
        hint = f"Rate limited by Instagram - retry after {when}. Avoid repeated bulk calls."
    elif code == 10 and "viewers" in meta_message.lower():
        hint = "Not enough viewers for Instagram to report insights on this story (needs at least 5)."
    elif code in PERMISSION_CODES:
        hint = (
            "Permission denied. Make sure the token has the required permissions (instagram_basic, "
            "instagram_manage_comments, instagram_manage_insights, pages_read_engagement) and that the "
            "Instagram account is assigned to the token's (system) user in Business settings."
        )
    elif code == 100 and subcode == 33:
        hint = "Object not found, or this token can't access it. Double-check the ID (use list_recent_media / get_media_comments to get valid IDs)."
    elif code == 100:
        hint = "Invalid request parameter."
    elif code == 368:
        hint = "Instagram temporarily blocked this action for policy reasons. Wait before retrying."
    elif code in TRANSIENT_CODES:
        hint = "Temporary Instagram API problem - retry in a minute."
    else:
        hint = "Instagram API error."
    return f"{hint} (Meta says: {meta_message} [code {code}{f'/{subcode}' if subcode else ''}])"


def from_response(response: httpx.Response, *secrets: str | None) -> MetaAPIError:
    """Build a MetaAPIError from an error HTTP response."""
    retry = _retry_after_minutes(response.headers)
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        body = redact(response.text[:200], *secrets)
        return MetaAPIError(
            f"Instagram API returned HTTP {response.status_code} with an unexpected body: {body}",
            http_status=response.status_code,
            retry_after_minutes=retry,
        )
    code = err.get("code")
    subcode = err.get("error_subcode")
    meta_message = redact(str(err.get("error_user_msg") or err.get("message") or "unknown error"), *secrets)
    return MetaAPIError(
        redact(friendly_message(code, subcode, meta_message, retry), *secrets),
        code=code,
        subcode=subcode,
        error_type=err.get("type"),
        meta_message=meta_message,
        http_status=response.status_code,
        retry_after_minutes=retry,
        fbtrace_id=err.get("fbtrace_id"),
    )
