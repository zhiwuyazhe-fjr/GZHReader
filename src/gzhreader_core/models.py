from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


SourceStatus = Literal["active", "paused", "limited", "error", "unsupported"]


@dataclass(slots=True)
class SourceProfile:
    id: str
    name: str
    avatar: str = ""
    intro: str = ""
    sample_url: str = ""
    status: SourceStatus = "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ArticleRecord:
    source_id: str
    origin_id: str
    title: str
    url: str
    published_at: datetime
    author: str = ""
    cover: str = ""
    content: str = ""
    digest: str = ""


@dataclass(slots=True)
class ProviderHealth:
    state: Literal["ready", "disconnected", "verification", "cooldown", "error"]
    message: str
    reconnect_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProviderBatch:
    articles: list[ArticleRecord]
    newest_cursor: int = 0
    reached_cursor: bool = True
    pages_scanned: int = 0


@dataclass(slots=True)
class SyncResult:
    source_count: int = 0
    fetched: int = 0
    inserted: int = 0
    summarized: int = 0
    content_pending: int = 0
    source_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
