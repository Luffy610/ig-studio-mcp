"""Trimmed response models — only the fields an LLM needs, never raw Meta JSON."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MetricValue = int | float


class Profile(BaseModel):
    id: str
    username: str
    name: str | None = None
    biography: str | None = None
    website: str | None = None
    followers_count: int | None = None
    follows_count: int | None = None
    media_count: int | None = None


class MediaItem(BaseModel):
    id: str
    caption: str | None = None
    media_type: str | None = Field(None, description="IMAGE, VIDEO or CAROUSEL_ALBUM")
    media_product_type: str | None = Field(None, description="FEED, REELS, STORY or AD")
    timestamp: str | None = None
    like_count: int | None = Field(None, description="Missing if the owner hid like counts")
    comments_count: int | None = None
    permalink: str | None = None


class MediaList(BaseModel):
    items: list[MediaItem]
    count: int
    has_more: bool = Field(description="True if older media exists beyond this page")


class Comment(BaseModel):
    id: str
    username: str | None = None
    text: str | None = None
    timestamp: str | None = None
    like_count: int | None = None
    hidden: bool | None = None
    reply_count: int = 0
    reply_count_is_partial: bool = Field(False, description="True if the comment has more replies than were counted")


class CommentList(BaseModel):
    media_id: str
    comments: list[Comment]
    count: int
    has_more: bool = Field(description="True if more top-level comments exist beyond this page")


class MediaInsights(BaseModel):
    media_id: str
    media_type: str | None = None
    media_product_type: str | None = None
    metrics: dict[str, MetricValue] = Field(description="Lifetime metric values. `saved` is reported as `saves`.")
    unavailable: list[str] = Field(default_factory=list, description="Metrics Instagram did not return for this media")
    notes: list[str] = Field(default_factory=list)


class AccountInsights(BaseModel):
    period: Literal["day", "week", "days_28"]
    since: str = Field(description="Window start, ISO 8601 UTC")
    until: str = Field(description="Window end, ISO 8601 UTC")
    metrics: dict[str, MetricValue] = Field(description="Totals over the window")
    unavailable: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CommentAction(BaseModel):
    """Preview (executed=false) or result (executed=true) of a comment write."""

    action: Literal["reply_to_comment", "hide_comment", "unhide_comment", "delete_comment"]
    comment_id: str
    executed: bool = Field(description="False = preview only; nothing was sent to Instagram")
    summary: str
    request: str = Field(description="The Graph API call that is (or would be) made")
    message: str | None = Field(None, description="Exact reply text")
    reply_id: str | None = Field(None, description="ID of the created reply")
    success: bool | None = None
    notes: list[str] = Field(default_factory=list)


class PublishAction(BaseModel):
    """Preview (executed=false) or result (executed=true) of publishing a post."""

    action: Literal["publish_image", "publish_carousel"]
    executed: bool = Field(description="False = preview only; nothing was sent to Instagram")
    summary: str
    image_urls: list[str]
    caption: str | None = None
    container_ids: list[str] = Field(default_factory=list, description="Media containers created (children first)")
    media_id: str | None = Field(None, description="ID of the published post")
    permalink: str | None = None
    notes: list[str] = Field(default_factory=list)


class PublishingLimit(BaseModel):
    """API publishing quota for the rolling 24 hours."""

    quota_usage: int = Field(description="Posts published through the API in the window")
    quota_total: int | None = Field(None, description="Maximum posts per window")
    quota_duration_hours: int | None = None
    remaining: int | None = None


class AccountRef(BaseModel):
    """An Instagram professional account the token can manage."""

    id: str
    username: str | None = None
    page_name: str | None = None


class TokenInfo(BaseModel):
    """Result of a token exchange. Never contains the token itself."""

    expires_in_days: int | None = None
