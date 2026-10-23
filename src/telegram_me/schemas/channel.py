"""Response models of channel pages, previews and pagination."""

from typing import Any

from pydantic import BaseModel, HttpUrl, RootModel, field_validator, model_validator

from telegram_me.schemas.post import ParsedAndRaw, Post


class Counter(BaseModel):
    """Channel statistics as formatted by Telegram (e.g. `1.2M`)."""

    subscribers: str
    photos: str | None = None
    videos: str | None = None
    files: str | None = None
    links: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _singular_subscriber_key(cls, values: Any) -> Any:
        if isinstance(values, dict) and "subscriber" in values:
            values = {**values, "subscribers": values["subscriber"]}
            del values["subscriber"]
        return values


class Channel(BaseModel):
    """Channel profile shown above the posts."""

    username: str
    title: ParsedAndRaw
    description: ParsedAndRaw | None
    avatar: HttpUrl | None = None
    counters: Counter
    labels: list[str] | None = None
    """Header labels, e.g. `verified`."""

    @field_validator("avatar", mode="before")
    @classmethod
    def _empty_avatar(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("labels", mode="before")
    @classmethod
    def _empty_labels(cls, value: object) -> object:
        return value or None


class OffsetItem(BaseModel):
    """Pagination cursors: pass them to `/v1/more` to load adjacent posts."""

    before: int | None = None
    after: int | None = None


class Meta(BaseModel):
    """Pagination metadata."""

    offset: OffsetItem


class Content(BaseModel):
    """Posts of a channel page."""

    posts: list[Post]


class ChannelBody(BaseModel):
    """Channel page: profile, posts and pagination."""

    channel: Channel
    content: Content
    meta: Meta


class More(BaseModel):
    """A batch of posts loaded relative to a position."""

    posts: list[Post]
    meta: Meta


class PreviewChannel(BaseModel):
    """Channel summary from the `t.me/<channel>` landing page."""

    title: str
    subscribers: str
    description: str | None = None
    avatar: HttpUrl | None = None
    is_verified: bool

    @field_validator("avatar", mode="before")
    @classmethod
    def _inline_avatar(cls, value: object) -> object:
        # Telegram sometimes embeds the avatar as a data URI instead of a URL.
        if isinstance(value, str) and value.startswith("data:"):
            return None
        return value


class Preview(BaseModel):
    """Channel preview."""

    channel: PreviewChannel | None = None


class Previews(RootModel[dict[str, Preview]]):
    """Previews keyed by the requested username; unknown channels are omitted."""
