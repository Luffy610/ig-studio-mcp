"""Async client for Meta's Instagram API with Unified Login (graph.facebook.com).

All HTTP calls live here. Methods return trimmed pydantic models from
`models.py`; failures raise `IGStudioError` subclasses with actionable messages.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from .auth import Credentials, TokenStatus, TokenStore, parse_token_status
from .errors import (
    ConfigError,
    IGStudioError,
    MetaAPIError,
    NetworkError,
    container_error_message,
    from_response,
    redact,
)
from .models import (
    AccountInsights,
    AccountRef,
    Comment,
    CommentAction,
    CommentList,
    MediaInsights,
    MediaItem,
    MediaList,
    MetricValue,
    Profile,
    PublishAction,
    PublishingLimit,
    TokenInfo,
)

# The single place the Graph API version is pinned.
GRAPH_API_VERSION = "v26.0"
BASE_URL = "https://graph.facebook.com"
API_URL = f"{BASE_URL}/{GRAPH_API_VERSION}"
API_HOST = "graph.facebook.com"

log = logging.getLogger(__name__)
# httpx logs full request URLs (which carry access_token) at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Permissions the tools rely on (Unified Login names). instagram_manage_insights is not
# in Meta's default configuration and has to be added by hand.
REQUIRED_SCOPES = (
    "instagram_basic",
    "instagram_manage_comments",
    "instagram_manage_insights",
    "instagram_content_publish",
)

PROFILE_FIELDS = "id,username,name,biography,website,followers_count,follows_count,media_count"
MEDIA_FIELDS = "id,caption,media_type,media_product_type,timestamp,like_count,comments_count,permalink"
COMMENT_FIELDS = "id,username,from,text,timestamp,like_count,hidden,replies{id}"

# Valid media insight metrics per media_product_type (Graph API v26.0).
# `impressions`, `plays`, `video_views` and `engagement` are deprecated and deliberately absent.
MEDIA_METRICS: dict[str, list[str]] = {
    "FEED": [
        "reach",
        "likes",
        "comments",
        "saved",
        "shares",
        "total_interactions",
        "views",
        "profile_visits",
        "follows",
    ],
    "REELS": [
        "reach",
        "likes",
        "comments",
        "saved",
        "shares",
        "total_interactions",
        "views",
        "ig_reels_avg_watch_time",
        "ig_reels_video_view_total_time",
    ],
    "STORY": [
        "reach",
        "shares",
        "total_interactions",
        "views",
        "replies",
        "navigation",
        "follows",
        "profile_visits",
    ],
}
METRIC_RENAMES = {"saved": "saves"}

ACCOUNT_METRICS = [
    "reach",
    "views",
    "accounts_engaged",
    "total_interactions",
    "likes",
    "comments",
    "shares",
    "saves",
    "replies",
    "profile_links_taps",
]
# follows_and_unfollows only returns a value with breakdown=follow_type, so it gets its own call.
FOLLOW_TYPE_NAMES = {"FOLLOWER": "follows", "NON_FOLLOWER": "unfollows"}
Period = Literal["day", "week", "days_28"]
PERIOD_DAYS: dict[str, int] = {"day": 1, "week": 7, "days_28": 28}

MAX_MEDIA_LIMIT = 100
MAX_COMMENT_LIMIT = 500
COMMENTS_PAGE_SIZE = 50  # Meta returns at most 50 comments per query
# Content publishing limits (Graph API v26.0 docs).
CAPTION_MAX_CHARS = 2200
CAPTION_MAX_HASHTAGS = 30
CAPTION_MAX_MENTIONS = 20
CAROUSEL_MIN_ITEMS = 2
CAROUSEL_MAX_ITEMS = 10  # the guide says 20, but error 2207028 says 10; stay on the safe side
# Seconds to wait before each container status check. Meta recommends polling for no more
# than 5 minutes; images are usually FINISHED on the first check.
CONTAINER_POLL_SCHEDULE = (0, 2, 3, 5, 10, 20, 30, 60, 60, 60)  # 250 s in total
TOKEN_STATUS_TTL = 24 * 3600  # re-check token expiry once a day
REFRESH_RETRY_SECONDS = 3600  # after a failed auto-refresh, wait before trying again


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_metric_values(payload: dict[str, Any]) -> dict[str, MetricValue]:
    """Extract {metric: number} from an insights response (lifetime or total_value)."""
    values: dict[str, MetricValue] = {}
    for item in payload.get("data", []):
        name = item.get("name")
        if not name:
            continue
        if isinstance(item.get("total_value"), dict):
            value = item["total_value"].get("value")
        elif item.get("values"):
            value = item["values"][-1].get("value")
        else:
            value = None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values[METRIC_RENAMES.get(name, name)] = value
    return values


def _parse_follow_breakdown(payload: dict[str, Any]) -> dict[str, MetricValue]:
    """follows_and_unfollows with breakdown=follow_type -> {"follows": n, "unfollows": n}."""
    values: dict[str, MetricValue] = {}
    for item in payload.get("data", []):
        for breakdown in (item.get("total_value") or {}).get("breakdowns", []):
            for result in breakdown.get("results", []):
                name = FOLLOW_TYPE_NAMES.get((result.get("dimension_values") or [None])[0])
                if name and isinstance(result.get("value"), (int, float)):
                    values[name] = result["value"]
    return values


def caption_problems(caption: str | None) -> list[str]:
    """Hard caption limits Instagram enforces; an empty list means the caption is fine."""
    if not caption:
        return []
    problems = []
    if len(caption) > CAPTION_MAX_CHARS:
        problems.append(f"Caption is {len(caption)} characters; the maximum is {CAPTION_MAX_CHARS}.")
    hashtags = len(re.findall(r"(?<![\w&])#\w+", caption))
    if hashtags > CAPTION_MAX_HASHTAGS:
        problems.append(f"Caption has {hashtags} hashtags; the maximum is {CAPTION_MAX_HASHTAGS}.")
    mentions = len(re.findall(r"(?<![\w.])@[\w.]+", caption))
    if mentions > CAPTION_MAX_MENTIONS:
        problems.append(f"Caption has {mentions} @mentions; the maximum is {CAPTION_MAX_MENTIONS}.")
    return problems


def image_url_problems(url: str) -> tuple[list[str], list[str]]:
    """(errors, warnings) for an image URL, checked locally without fetching it."""
    errors: list[str] = []
    warnings: list[str] = []
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        errors.append(f"{url!r} is not an http(s) URL. Instagram downloads the image itself, so it must be public.")
        return errors, warnings
    if not parsed.path.lower().endswith((".jpg", ".jpeg")):
        warnings.append(f"{url} doesn't end in .jpg/.jpeg. Instagram only accepts JPEG images.")
    if not url.isascii():
        warnings.append(f"{url} contains non-ASCII characters; Meta recommends ASCII-only URLs.")
    return errors, warnings


def _to_comment(raw: dict[str, Any]) -> Comment:
    replies = raw.get("replies") or {}
    return Comment(
        id=raw["id"],
        username=raw.get("username") or (raw.get("from") or {}).get("username"),
        text=raw.get("text"),
        timestamp=raw.get("timestamp"),
        like_count=raw.get("like_count"),
        hidden=raw.get("hidden"),
        reply_count=len(replies.get("data", [])),
        reply_count_is_partial=bool((replies.get("paging") or {}).get("next")),
    )


class IGClient:
    def __init__(
        self,
        store: TokenStore | None = None,
        http: httpx.AsyncClient | None = None,
        *,
        auto_refresh: bool = True,
    ) -> None:
        self.store = store or TokenStore()
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._owns_http = http is None
        self.auto_refresh = auto_refresh
        self._refresh_lock = asyncio.Lock()
        self._next_refresh_attempt = 0.0
        self._status_cache: tuple[str, float, TokenStatus | None] | None = None
        self._user_id: str | None = None
        self._sleep = asyncio.sleep  # swapped out in tests

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> IGClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ plumbing

    async def _credentials(self) -> Credentials:
        creds = self.store.load()
        if not self.auto_refresh or time.monotonic() < self._next_refresh_attempt:
            return creds
        status = await self._cached_status(creds)
        if status is None or not status.needs_refresh():
            return creds
        async with self._refresh_lock:
            creds = self.store.load()
            if time.monotonic() < self._next_refresh_attempt:
                return creds
            try:
                await self.refresh_access_token(creds)
                # Meta doesn't document whether re-exchanging extends a long-lived token,
                # so never re-exchange more than once a day even if it still looks close to expiry.
                self._next_refresh_attempt = time.monotonic() + TOKEN_STATUS_TTL
                log.info("Access token was close to expiry and has been re-exchanged.")
                return self.store.load()
            except IGStudioError as exc:
                # The current token keeps working until it expires; try again later.
                self._next_refresh_attempt = time.monotonic() + REFRESH_RETRY_SECONDS
                log.warning("Token expires %s and could not be renewed: %s", status.expires_at, exc)
        return creds

    async def _cached_status(self, creds: Credentials) -> TokenStatus | None:
        """debug_token result for this token, cached for a day; None if it can't be read."""
        cache = self._status_cache
        if cache and cache[0] == creds.access_token and time.monotonic() - cache[1] < TOKEN_STATUS_TTL:
            return cache[2]
        try:
            status: TokenStatus | None = await self.token_status(creds)
        except IGStudioError as exc:
            log.info("Could not read token expiry via debug_token: %s", exc)
            status = None
        self._status_cache = (creds.access_token, time.monotonic(), status)
        return status

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        creds: Credentials | None = None,
        authenticate: bool = True,
    ) -> dict[str, Any]:
        creds = creds or await self._credentials()
        token = creds.access_token
        query = dict(params or {})
        if authenticate:
            query["access_token"] = token
            if creds.appsecret_proof:
                query["appsecret_proof"] = creds.appsecret_proof
        url = f"{API_URL}/{path.lstrip('/')}"
        # POST parameters (including the token) go in the form body, everything else in the query string.
        body_or_query = {"data": query} if method == "POST" else {"params": query}
        try:
            response = await self._http.request(method, url, **body_or_query)
        except httpx.TimeoutException as exc:
            raise NetworkError(f"Timed out talking to {API_HOST} - retry shortly.") from exc
        except httpx.HTTPError as exc:
            raise NetworkError(
                redact(f"Could not reach {API_HOST} ({type(exc).__name__}: {exc}). Check your connection.", token)
            ) from exc

        if response.status_code >= 400:
            raise from_response(response, token, creds.app_secret)
        try:
            payload = response.json()
        except ValueError:
            raise from_response(response, token, creds.app_secret) from None
        if isinstance(payload, dict) and "error" in payload:
            raise from_response(response, token, creds.app_secret)
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params)

    async def _paginate(
        self, path: str, params: dict[str, Any], limit: int, page_size: int
    ) -> tuple[list[dict[str, Any]], bool]:
        """Follow cursor pagination until `limit` items are collected."""
        items: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            page_params = {**params, "limit": min(page_size, limit - len(items))}
            if after:
                page_params["after"] = after
            payload = await self._get(path, page_params)
            data = payload.get("data", [])
            items.extend(data)
            paging = payload.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            more_available = bool(paging.get("next") and after and data)
            if len(items) >= limit:
                return items[:limit], more_available or len(items) > limit
            if not more_available:
                return items, False

    async def _fetch_metrics(
        self, path: str, metrics: list[str], params: dict[str, Any]
    ) -> tuple[dict[str, MetricValue], list[str]]:
        """Request metrics in one call; if Meta rejects the set, retry one by one to salvage what it supports."""
        try:
            payload = await self._get(path, {**params, "metric": ",".join(metrics)})
            values = _parse_metric_values(payload)
        except MetaAPIError as exc:
            if not exc.is_invalid_parameter or len(metrics) == 1:
                raise
            log.info("Combined metric request rejected; retrying metrics individually.")
            results = await asyncio.gather(
                *(self._get(path, {**params, "metric": m}) for m in metrics), return_exceptions=True
            )
            values = {}
            for result in results:
                if isinstance(result, MetaAPIError) and result.is_invalid_parameter:
                    continue
                if isinstance(result, BaseException):
                    raise result from exc
                values.update(_parse_metric_values(result))
        unavailable = [m for m in metrics if METRIC_RENAMES.get(m, m) not in values]
        return values, unavailable

    # ------------------------------------------------------------------ account discovery

    async def list_instagram_accounts(self) -> list[AccountRef]:
        """Instagram professional accounts this token can manage.

        System-user tokens see accounts assigned to them in Business settings
        (`/me/assigned_instagram_accounts`); user tokens see accounts linked to
        Pages they manage (`/me/accounts`).
        """
        accounts: dict[str, AccountRef] = {}
        try:
            assigned = await self._get("me/assigned_instagram_accounts", {"fields": "id,username", "limit": 100})
            for item in assigned.get("data", []):
                accounts[item["id"]] = AccountRef(id=item["id"], username=item.get("username"))
        except MetaAPIError as exc:
            if not (exc.is_invalid_parameter or exc.is_permission):
                raise
            log.info("assigned_instagram_accounts unavailable for this token; trying Pages.")
        if not accounts:
            pages = await self._get(
                "me/accounts", {"fields": "id,name,instagram_business_account{id,username}", "limit": 100}
            )
            for page in pages.get("data", []):
                ig = page.get("instagram_business_account")
                if ig and ig.get("id"):
                    accounts[ig["id"]] = AccountRef(
                        id=ig["id"], username=ig.get("username"), page_name=page.get("name")
                    )
        return list(accounts.values())

    async def _user_path(self) -> str:
        """The Instagram account ID: IG_ACCOUNT_ID if set, else discovered from the token (cached)."""
        if self._user_id is None:
            creds = await self._credentials()
            self._user_id = creds.user_id or await self._discover_account()
        return self._user_id

    async def _discover_account(self) -> str:
        accounts = await self.list_instagram_accounts()
        if len(accounts) == 1:
            return accounts[0].id
        if not accounts:
            raise ConfigError(
                "No Instagram professional account is reachable with this token. For a system-user token, "
                "assign the Instagram account to the system user (Business settings -> Users -> System users "
                "-> Assign assets). For a user token, link the Instagram account to a Facebook Page you manage."
            )
        listed = ", ".join(f"@{a.username} ({a.id})" for a in accounts)
        raise ConfigError(
            f"This token can manage several Instagram accounts: {listed}. Set IG_ACCOUNT_ID in .env to pick one."
        )

    # ------------------------------------------------------------------ read API

    async def get_profile(self) -> Profile:
        payload = await self._get(await self._user_path(), {"fields": PROFILE_FIELDS})
        return Profile.model_validate(payload)

    async def list_recent_media(self, limit: int = 10) -> MediaList:
        limit = _clamp(limit, 1, MAX_MEDIA_LIMIT)
        raw, has_more = await self._paginate(
            f"{await self._user_path()}/media", {"fields": MEDIA_FIELDS}, limit, page_size=limit
        )
        items = [MediaItem.model_validate(item) for item in raw]
        return MediaList(items=items, count=len(items), has_more=has_more)

    async def get_media_comments(self, media_id: str, limit: int = 50) -> CommentList:
        limit = _clamp(limit, 1, MAX_COMMENT_LIMIT)
        raw, has_more = await self._paginate(
            f"{media_id}/comments", {"fields": COMMENT_FIELDS}, limit, page_size=COMMENTS_PAGE_SIZE
        )
        comments = [_to_comment(item) for item in raw]
        return CommentList(media_id=media_id, comments=comments, count=len(comments), has_more=has_more)

    async def get_media_insights(self, media_id: str) -> MediaInsights:
        info = await self._get(media_id, {"fields": "id,media_type,media_product_type"})
        media_type = info.get("media_type")
        product = info.get("media_product_type")
        notes: list[str] = []
        if not product:
            product = "REELS" if media_type == "VIDEO" else "FEED"
            notes.append(f"media_product_type not returned; assumed {product} from media_type={media_type}.")
        metrics = MEDIA_METRICS.get(product, MEDIA_METRICS["FEED"])
        values, unavailable = await self._fetch_metrics(f"{media_id}/insights", metrics, {})
        if product == "STORY":
            notes.append("Story insights are only available for 24 hours and need at least 5 viewers.")
        notes.append("Insights can lag up to 48 hours behind real activity.")
        return MediaInsights(
            media_id=media_id,
            media_type=media_type,
            media_product_type=product,
            metrics=values,
            unavailable=unavailable,
            notes=notes,
        )

    async def _follow_counts(self, path: str, params: dict[str, Any]) -> dict[str, MetricValue]:
        """New follows and unfollows over the window; empty if Instagram won't report them."""
        try:
            payload = await self._get(path, {**params, "metric": "follows_and_unfollows", "breakdown": "follow_type"})
        except MetaAPIError as exc:
            if exc.is_invalid_parameter or exc.is_permission:
                return {}
            raise
        # A successful response with no breakdown rows means nobody followed or unfollowed.
        return {"follows": 0, "unfollows": 0, **_parse_follow_breakdown(payload)}

    async def get_account_insights(self, period: Period = "day", *, now: datetime | None = None) -> AccountInsights:
        if period not in PERIOD_DAYS:
            raise ConfigError(f"period must be one of {sorted(PERIOD_DAYS)}, got {period!r}.")
        until = (now or datetime.now(UTC)).replace(microsecond=0)
        since = until - timedelta(days=PERIOD_DAYS[period])
        params = {
            "period": "day",
            "metric_type": "total_value",
            "since": int(since.timestamp()),
            "until": int(until.timestamp()),
        }
        path = f"{await self._user_path()}/insights"
        (values, unavailable), follows = await asyncio.gather(
            self._fetch_metrics(path, ACCOUNT_METRICS, params),
            self._follow_counts(path, params),
        )
        values.update(follows)
        if not follows:
            unavailable.append("follows_and_unfollows")
        notes = ["Totals over the window. Data can lag up to 48 hours."]
        if unavailable:
            notes.append("Some metrics (e.g. follows/unfollows) need 100+ followers or recent activity.")
        return AccountInsights(
            period=period,
            since=_iso(since),
            until=_iso(until),
            metrics=values,
            unavailable=unavailable,
            notes=notes,
        )

    # ------------------------------------------------------------------ comment moderation

    async def reply_to_comment(self, comment_id: str, message: str) -> CommentAction:
        payload = await self._request("POST", f"{comment_id}/replies", {"message": message})
        return CommentAction(
            action="reply_to_comment",
            comment_id=comment_id,
            executed=True,
            success=bool(payload.get("id")),
            summary=f"Replied to comment {comment_id}.",
            request=f"POST /{GRAPH_API_VERSION}/{comment_id}/replies",
            message=message,
            reply_id=payload.get("id"),
        )

    async def hide_comment(self, comment_id: str, hide: bool = True) -> CommentAction:
        payload = await self._request("POST", comment_id, {"hide": "true" if hide else "false"})
        verb = "Hid" if hide else "Unhid"
        return CommentAction(
            action="hide_comment" if hide else "unhide_comment",
            comment_id=comment_id,
            executed=True,
            success=bool(payload.get("success")),
            summary=f"{verb} comment {comment_id}.",
            request=f"POST /{GRAPH_API_VERSION}/{comment_id}?hide={str(hide).lower()}",
        )

    async def delete_comment(self, comment_id: str) -> CommentAction:
        payload = await self._request("DELETE", comment_id)
        return CommentAction(
            action="delete_comment",
            comment_id=comment_id,
            executed=True,
            success=bool(payload.get("success")),
            summary=f"Deleted comment {comment_id}.",
            request=f"DELETE /{GRAPH_API_VERSION}/{comment_id}",
        )

    # ------------------------------------------------------------------ publishing

    async def get_publishing_limit(self) -> PublishingLimit:
        """API posts published in the rolling 24 hours, and the cap."""
        payload = await self._get(
            f"{await self._user_path()}/content_publishing_limit", {"fields": "config,quota_usage"}
        )
        entry = (payload.get("data") or [{}])[0]
        config = entry.get("config") or {}
        usage = int(entry.get("quota_usage") or 0)
        total = config.get("quota_total")
        duration = config.get("quota_duration")
        return PublishingLimit(
            quota_usage=usage,
            quota_total=total,
            quota_duration_hours=int(duration) // 3600 if isinstance(duration, (int, float)) else None,
            remaining=max(total - usage, 0) if isinstance(total, int) else None,
        )

    async def _create_image_container(
        self, image_url: str, caption: str | None = None, *, is_carousel_item: bool = False
    ) -> str:
        params: dict[str, Any] = {"image_url": image_url}
        if is_carousel_item:
            params["is_carousel_item"] = "true"  # children never carry a caption
        elif caption:
            params["caption"] = caption
        payload = await self._request("POST", f"{await self._user_path()}/media", params)
        return str(payload["id"])

    async def _create_carousel_container(self, child_ids: list[str], caption: str | None) -> str:
        params: dict[str, Any] = {"media_type": "CAROUSEL", "children": ",".join(child_ids)}
        if caption:
            params["caption"] = caption
        payload = await self._request("POST", f"{await self._user_path()}/media", params)
        return str(payload["id"])

    async def _wait_until_finished(self, container_id: str) -> None:
        """Poll a container's status_code until FINISHED (at most ~5 minutes)."""
        for delay in CONTAINER_POLL_SCHEDULE:
            if delay:
                await self._sleep(delay)
            payload = await self._get(container_id, {"fields": "status_code,status"})
            code = payload.get("status_code")
            if code == "FINISHED":
                return
            if code in ("IN_PROGRESS", None):
                continue
            if code == "ERROR":
                raise MetaAPIError(container_error_message(payload.get("status")))
            if code == "EXPIRED":
                raise MetaAPIError(
                    f"Media container {container_id} expired (not published within 24 hours). Publish again."
                )
            if code == "PUBLISHED":
                raise MetaAPIError(f"Media container {container_id} was already published.")
            raise MetaAPIError(f"Media container {container_id} is in an unexpected state: {code}.")
        raise MetaAPIError(
            f"Instagram is still processing media container {container_id} after 5 minutes. "
            "Nothing was published; try again later."
        )

    async def _publish_container(self, container_id: str) -> tuple[str, list[str]]:
        """Publish a FINISHED container; return (media_id, notes)."""
        payload = await self._request("POST", f"{await self._user_path()}/media_publish", {"creation_id": container_id})
        notes = [
            f"Published, but Instagram reported {key}={payload[key]}."
            for key in ("config_issue", "caption_add_on_attachment_status")
            if payload.get(key)
        ]
        return str(payload["id"]), notes

    async def _permalink(self, media_id: str) -> str | None:
        try:
            return (await self._get(media_id, {"fields": "permalink"})).get("permalink")
        except IGStudioError as exc:
            log.info("Published, but could not read the permalink: %s", exc)
            return None

    @staticmethod
    def _check_post(image_urls: list[str], caption: str | None) -> None:
        problems = caption_problems(caption)
        for url in image_urls:
            problems += image_url_problems(url)[0]
        if problems:
            raise ConfigError(" ".join(problems))

    async def publish_image(self, image_url: str, caption: str | None = None) -> PublishAction:
        """Publish a single image post: container -> wait until FINISHED -> media_publish."""
        self._check_post([image_url], caption)
        container_id = await self._create_image_container(image_url, caption)
        await self._wait_until_finished(container_id)
        media_id, notes = await self._publish_container(container_id)
        return PublishAction(
            action="publish_image",
            executed=True,
            summary="Published the image to Instagram.",
            image_urls=[image_url],
            caption=caption,
            container_ids=[container_id],
            media_id=media_id,
            permalink=await self._permalink(media_id),
            notes=notes,
        )

    async def publish_carousel(self, image_urls: list[str], caption: str | None = None) -> PublishAction:
        """Publish a carousel: one child container per image, then the parent, then media_publish."""
        if not CAROUSEL_MIN_ITEMS <= len(image_urls) <= CAROUSEL_MAX_ITEMS:
            raise ConfigError(
                f"A carousel needs {CAROUSEL_MIN_ITEMS}-{CAROUSEL_MAX_ITEMS} images; got {len(image_urls)}."
            )
        self._check_post(image_urls, caption)
        child_ids = list(
            await asyncio.gather(*(self._create_image_container(url, is_carousel_item=True) for url in image_urls))
        )
        await asyncio.gather(*(self._wait_until_finished(child) for child in child_ids))
        parent_id = await self._create_carousel_container(child_ids, caption)
        await self._wait_until_finished(parent_id)
        media_id, notes = await self._publish_container(parent_id)
        return PublishAction(
            action="publish_carousel",
            executed=True,
            summary=f"Published a {len(image_urls)}-image carousel to Instagram.",
            image_urls=image_urls,
            caption=caption,
            container_ids=[*child_ids, parent_id],
            media_id=media_id,
            permalink=await self._permalink(media_id),
            notes=notes,
        )

    # ------------------------------------------------------------------ tokens

    async def token_status(self, creds: Credentials | None = None) -> TokenStatus:
        """Ask debug_token about the configured token (type, expiry, scopes)."""
        creds = creds or self.store.load()
        payload = await self._request("GET", "debug_token", {"input_token": creds.access_token}, creds=creds)
        return parse_token_status(payload.get("data") or {})

    async def refresh_access_token(self, creds: Credentials | None = None) -> TokenInfo:
        """Re-exchange the current user token for a fresh long-lived one (needs META_APP_ID and META_APP_SECRET)."""
        creds = creds or self.store.load()
        return await self._exchange(creds.access_token, creds.app_id, creds.app_secret)

    async def exchange_short_lived_token(self, short_lived_token: str) -> TokenInfo:
        """Swap a short-lived user token for a ~60-day long-lived one and save it to .env."""
        return await self._exchange(
            short_lived_token.strip(), self.store.value("META_APP_ID"), self.store.value("META_APP_SECRET")
        )

    async def _exchange(self, token: str, app_id: str | None, app_secret: str | None) -> TokenInfo:
        if not (app_id and app_secret):
            raise ConfigError(
                "META_APP_ID and META_APP_SECRET must be set in .env to exchange tokens "
                "(Meta app dashboard -> App settings -> Basic)."
            )
        payload = await self._request(
            "GET",
            "oauth/access_token",
            {
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": token,
            },
            creds=Credentials(access_token=token, app_secret=app_secret),
            authenticate=False,
        )
        new_token = payload.get("access_token")
        if not new_token:
            raise MetaAPIError("Meta did not return an access token.")
        self.store.save_token(new_token)
        self._status_cache = None
        expires_in = payload.get("expires_in")
        return TokenInfo(expires_in_days=int(expires_in) // 86400 if isinstance(expires_in, (int, float)) else None)
