"""MCP tool definitions — thin wrappers over `IGClient`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Literal, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .client import (
    CAROUSEL_MAX_ITEMS,
    CAROUSEL_MIN_ITEMS,
    GRAPH_API_VERSION,
    IGClient,
    caption_problems,
    image_url_problems,
)
from .errors import IGStudioError
from .models import (
    AccountInsights,
    AudienceDemographics,
    CommentAction,
    CommentList,
    FollowerActiveHours,
    MediaInsights,
    MediaList,
    Profile,
    PublishAction,
    PublishingLimit,
)

T = TypeVar("T")

_client: IGClient | None = None


def get_client() -> IGClient:
    global _client
    if _client is None:
        _client = IGClient()
    return _client


def set_client(client: IGClient | None) -> None:
    """Swap the client (used by tests)."""
    global _client
    _client = client


@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
    try:
        yield None
    finally:
        if _client is not None:
            await _client.aclose()


mcp = MCPServer(
    name="ig-studio",
    instructions=(
        "Tools for the user's own Instagram Creator/Business account via Meta's official API. "
        "Start with get_profile or list_recent_media to discover IDs; media and comment IDs are "
        "required by the other tools. All data is the account owner's own."
    ),
    lifespan=lifespan,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
REVERSIBLE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)

CONFIRM_HINT = "Nothing was sent. Show this to the user and call again with confirm=true only after they approve."


def _require_id(comment_id: str) -> str:
    comment_id = comment_id.strip()
    if not comment_id:
        raise ToolError("comment_id is required - get it from get_media_comments.")
    return comment_id


async def _run(call: Awaitable[T]) -> T:
    try:
        return await call
    except IGStudioError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
async def get_profile() -> Profile:
    """Get the connected Instagram account's profile.

    Use this first to confirm which account is connected, or when the user asks about
    their follower count, bio, or how many posts they have.

    Returns: id, username, name, biography, website, followers_count, follows_count, media_count.
    """
    return await _run(get_client().get_profile())


@mcp.tool(annotations=READ_ONLY)
async def list_recent_media(limit: int = 10) -> MediaList:
    """List the account's most recent posts and reels (newest first). Stories are not included.

    Use this to find media IDs for get_media_comments / get_media_insights, or to review
    recent content and its engagement at a glance.

    Args:
        limit: How many items to return, 1-100 (default 10).

    Returns: items[] with id, caption, media_type (IMAGE/VIDEO/CAROUSEL_ALBUM),
    media_product_type (FEED/REELS), timestamp (UTC), like_count (absent if likes are hidden),
    comments_count, permalink; plus has_more if older media exists.
    """
    return await _run(get_client().list_recent_media(limit))


@mcp.tool(annotations=READ_ONLY)
async def get_media_comments(media_id: str, limit: int = 50) -> CommentList:
    """Get top-level comments on one post or reel, newest first.

    Use this to read audience feedback or find comment IDs to reply to / moderate.
    Get media_id from list_recent_media. Replies are not listed, only counted.

    Args:
        media_id: The Instagram media ID.
        limit: Max comments to return, 1-500 (default 50).

    Returns: comments[] with id, username, text, timestamp, like_count, hidden, reply_count
    (reply_count_is_partial=true means there are more replies than counted); has_more if
    more comments exist. Comments on age-gated media and comments hidden with hide_comment
    are not returned by Instagram.
    """
    return await _run(get_client().get_media_comments(media_id, limit))


@mcp.tool(annotations=READ_ONLY)
async def get_media_insights(media_id: str) -> MediaInsights:
    """Get lifetime performance metrics for one post, reel or story.

    Use this when the user asks how a specific piece of content performed. Get media_id
    from list_recent_media. Metrics depend on the media type and are picked automatically:
    - Feed posts/carousels: reach, views, likes, comments, saves, shares, total_interactions, profile_visits, follows
    - Reels: reach, views, likes, comments, saves, shares, total_interactions,
      ig_reels_avg_watch_time and ig_reels_video_view_total_time (milliseconds)
    - Stories: reach, views, shares, replies, navigation, follows, profile_visits, total_interactions

    Returns: metrics {name: value}, `unavailable` lists metrics Instagram didn't return,
    and `notes`. Data can lag up to 48 hours; items inside a carousel have no insights.
    """
    return await _run(get_client().get_media_insights(media_id))


@mcp.tool(annotations=READ_ONLY)
async def get_account_insights(period: Literal["day", "week", "days_28"] = "day") -> AccountInsights:
    """Get account-level totals for a recent window: the last 1, 7 or 28 days ending now.

    Use this when the user asks how their account is doing overall (reach, engagement,
    follower growth) rather than about a single post.

    Args:
        period: "day" (last 24h), "week" (last 7 days) or "days_28" (last 28 days).

    Returns: metrics {name: total} — reach, views, accounts_engaged, total_interactions,
    likes, comments, shares, saves, replies, profile_links_taps, follows (new followers)
    and unfollows —
    plus `unavailable` and `notes`. Some metrics need 100+ followers. Data can lag up to 48 hours.
    """
    return await _run(get_client().get_account_insights(period))


@mcp.tool(annotations=READ_ONLY)
async def get_audience_demographics(
    audience: Literal["followers", "engaged", "reached"] = "followers",
    breakdown: Literal["all", "age", "gender", "country", "city", "age_gender"] = "all",
    timeframe: Literal["this_week", "this_month"] = "this_month",
) -> AudienceDemographics:
    """Who the audience is: age, gender, country and city breakdowns.

    Use when the user asks about their followers' or viewers' demographics: age groups,
    gender split, top countries or cities.

    Args:
        audience: "followers" (current followers, default), "engaged" (accounts that interacted
            with content in the timeframe) or "reached" (accounts that saw content in the timeframe).
        breakdown: "all" (age, gender, country and city; default), one of those, or "age_gender"
            for the combined split (e.g. "25-34, male").
        timeframe: "this_week" or "this_month" (default). Affects engaged and reached only.

    Returns: breakdowns {name: [{label, value, percent}]} sorted largest first, plus `unavailable`
    and `notes`. Countries are ISO codes (IN, US). Cities are limited to the top 45.
    Limits: needs 100+ followers (or 100+ engagements in the timeframe for engaged/reached).
    """
    return await _run(get_client().get_audience_demographics(audience, breakdown, timeframe))


@mcp.tool(annotations=READ_ONLY)
async def get_follower_active_hours(days: int = 30, timezone: str = "UTC") -> FollowerActiveHours:
    """When followers are online: average followers on Instagram for each hour of the day.

    Use when the user asks when their followers are most active or the best time to post.
    Pass the user's own timezone so the hours are local to them.

    Args:
        days: How many recent days to average, 1-30 (default 30, the maximum Instagram allows).
        timezone: IANA timezone for the hours, e.g. "Asia/Kolkata", "America/New_York" (default "UTC").

    Returns: hours [{hour, avg_online_followers}] for 0-23, best_hours (top 3, best first),
    days_with_data and notes.
    Limits: needs 100+ followers; Instagram keeps only the last 30 days of this data.
    """
    return await _run(get_client().get_follower_active_hours(days, timezone))


@mcp.tool(annotations=WRITE)
async def reply_to_comment(comment_id: str, message: str, confirm: bool = False) -> CommentAction:
    """Post a public reply to a comment on the user's media, as the user's account.

    Use when the user wants to answer a comment. Get comment_id from get_media_comments.
    ALWAYS call with confirm=false first: that returns a preview and sends nothing. Show the
    preview to the user and only call again with confirm=true after they explicitly approve.

    Args:
        comment_id: The comment to reply to.
        message: Exact reply text (posted as-is, publicly).
        confirm: false = preview only (default); true = actually post.

    Returns: executed (false for previews), summary, the exact message, and reply_id once posted.
    Limits: replies always attach to the top-level comment (replying to a reply adds to its
    parent's thread); hidden comments and comments on live videos can't be replied to.
    """
    comment_id = _require_id(comment_id)
    message = message.strip()
    if not message:
        raise ToolError("message is empty - provide the reply text.")
    if not confirm:
        return CommentAction(
            action="reply_to_comment",
            comment_id=comment_id,
            executed=False,
            summary=f"Would publicly reply to comment {comment_id} with the message below.",
            request=f"POST /{GRAPH_API_VERSION}/{comment_id}/replies",
            message=message,
            notes=[
                CONFIRM_HINT,
                "If this comment is itself a reply, the reply is added to the top-level comment's thread.",
            ],
        )
    return await _run(get_client().reply_to_comment(comment_id, message))


@mcp.tool(annotations=REVERSIBLE)
async def hide_comment(comment_id: str, hide: bool = True, confirm: bool = False) -> CommentAction:
    """Hide (or unhide) a comment on the user's media. Reversible.

    Hidden comments disappear for everyone except their author. Prefer this over
    delete_comment for spam or abuse, since it can be undone with hide=false.
    ALWAYS call with confirm=false first to get a preview (nothing is sent), and only call
    with confirm=true after the user approves.

    Args:
        comment_id: The comment to hide or unhide (from get_media_comments).
        hide: true to hide (default), false to unhide.
        confirm: false = preview only (default); true = actually do it.

    Returns: executed (false for previews), summary and success.
    Limits: comments the account owner wrote on their own media always stay visible.
    Hidden comments no longer appear in get_media_comments, so tell the user the comment_id
    if they may want to unhide it later.
    """
    comment_id = _require_id(comment_id)
    if not confirm:
        verb = "hide" if hide else "unhide"
        return CommentAction(
            action="hide_comment" if hide else "unhide_comment",
            comment_id=comment_id,
            executed=False,
            summary=f"Would {verb} comment {comment_id}.",
            request=f"POST /{GRAPH_API_VERSION}/{comment_id}?hide={str(hide).lower()}",
            notes=[CONFIRM_HINT, "Reversible: hidden comments can be shown again with hide=false."],
        )
    return await _run(get_client().hide_comment(comment_id, hide))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_comment(comment_id: str, confirm: bool = False) -> CommentAction:
    """Permanently delete a comment on the user's media. Cannot be undone.

    Only use when the user clearly wants the comment gone; suggest hide_comment as the
    reversible alternative. ALWAYS call with confirm=false first to get a preview (nothing
    is sent), and only call with confirm=true after the user explicitly approves deleting it.

    Args:
        comment_id: The comment to delete (from get_media_comments).
        confirm: false = preview only (default); true = permanently delete.

    Returns: executed (false for previews), summary and success.
    Limits: only works on comments on the user's own media; live-video comments are not supported.
    """
    comment_id = _require_id(comment_id)
    if not confirm:
        return CommentAction(
            action="delete_comment",
            comment_id=comment_id,
            executed=False,
            summary=f"Would permanently delete comment {comment_id}. This cannot be undone.",
            request=f"DELETE /{GRAPH_API_VERSION}/{comment_id}",
            notes=[CONFIRM_HINT, "Consider hide_comment instead - it is reversible."],
        )
    return await _run(get_client().delete_comment(comment_id))


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def _post_preview(action: str, image_urls: list[str], caption: str | None) -> PublishAction:
    """Validate locally and describe the post. Makes no HTTP calls."""
    problems = caption_problems(caption)
    warnings: list[str] = []
    for url in image_urls:
        errors, url_warnings = image_url_problems(url)
        problems += errors
        warnings += url_warnings
    if problems:
        raise ToolError(" ".join(problems))
    count = len(image_urls)
    what = "one image" if action == "publish_image" else f"a {count}-image carousel"
    return PublishAction(
        action=action,  # type: ignore[arg-type]
        executed=False,
        summary=f"Would publicly post {what} to the feed with the caption below.",
        image_urls=image_urls,
        caption=caption,
        notes=[
            CONFIRM_HINT,
            "Posts go live immediately and publicly.",
            "Images that aren't JPEG, are over 8 MB, or are outside 4:5-1.91:1 will be rejected by Instagram.",
            *warnings,
        ],
    )


def _clean_caption(caption: str | None) -> str | None:
    return (caption or "").strip() or None


@mcp.tool(annotations=WRITE)
async def publish_image(image_url: str, caption: str | None = None, confirm: bool = False) -> PublishAction:
    """Publish a single photo to the user's Instagram feed. Public and immediate.

    Use only when the user explicitly asks to post. ALWAYS call with confirm=false first: that
    returns a preview (image URL, exact caption, warnings) and sends nothing. Show it to the
    user and call again with confirm=true only after they approve.

    Args:
        image_url: Public URL of a JPEG that Instagram can download (no login or expiring link).
            Max 8 MB, aspect ratio between 4:5 and 1.91:1, width 320-1440 px.
        caption: Post text. Max 2,200 characters, 30 hashtags, 20 @mentions.
        confirm: false = preview only (default); true = publish.

    Returns: executed, summary, container_ids, and media_id + permalink once published.
    Limits: a capped number of API posts per rolling 24 hours (see get_publishing_limit).
    Publishing usually takes seconds but can take up to ~5 minutes while Instagram processes it.
    """
    image_url = image_url.strip()
    caption = _clean_caption(caption)
    preview = _post_preview("publish_image", [image_url], caption)
    if not confirm:
        return preview
    return await _run(get_client().publish_image(image_url, caption))


@mcp.tool(annotations=WRITE)
async def publish_carousel(image_urls: list[str], caption: str | None = None, confirm: bool = False) -> PublishAction:
    """Publish a carousel (several photos in one post) to the user's feed. Public and immediate.

    Use only when the user explicitly asks to post several photos together. ALWAYS call with
    confirm=false first to get a preview (nothing is sent), and call with confirm=true only
    after the user approves.

    Args:
        image_urls: 2-10 public JPEG URLs, in display order. Every image is cropped to the
            first image's aspect ratio.
        caption: Post text, same limits as publish_image.
        confirm: false = preview only (default); true = publish.

    Returns: executed, summary, container_ids (children then parent), and media_id + permalink.
    Limits: counts as one post toward the daily limit.
    """
    image_urls = [url.strip() for url in image_urls]
    caption = _clean_caption(caption)
    if not CAROUSEL_MIN_ITEMS <= len(image_urls) <= CAROUSEL_MAX_ITEMS:
        raise ToolError(f"A carousel needs {CAROUSEL_MIN_ITEMS}-{CAROUSEL_MAX_ITEMS} images; got {len(image_urls)}.")
    preview = _post_preview("publish_carousel", image_urls, caption)
    if len(set(image_urls)) != len(image_urls):
        preview.notes.append("Some image URLs are repeated; the same photo will appear more than once.")
    if not confirm:
        return preview
    return await _run(get_client().publish_carousel(image_urls, caption))


@mcp.tool(annotations=READ_ONLY)
async def get_publishing_limit() -> PublishingLimit:
    """Check how many posts the account can still publish through the API in the rolling 24 hours.

    Use before publishing, or when a publish fails with a daily-limit error.

    Returns: quota_usage (posts published via the API in the window), quota_total,
    quota_duration_hours and remaining.
    """
    return await _run(get_client().get_publishing_limit())
