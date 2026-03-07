from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


@dataclass(slots=True)
class FeedArticle:
    feed_name: str
    feed_url: str
    title: str
    url: str
    author: str
    published_at: datetime
    content_html: str
    content_text: str
    summary_html: str
    summary_text: str


@dataclass(slots=True)
class ArticleDraft:
    feed_name: str
    feed_url: str
    title: str
    author: str
    publish_time: datetime
    url: str
    full_content: str
    raw_html: str
    content_source: str
    capture_status: str
    fingerprint: str
    summary: str = ""
    summary_status: str = "pending"
    summary_error: str = ""


@dataclass(slots=True)
class StoredArticle:
    id: int
    feed_name: str
    feed_url: str
    title: str
    author: str
    publish_time: datetime
    url: str
    full_content: str
    raw_html: str
    content_source: str
    capture_status: str
    summary: str
    summary_status: str
    summary_error: str


@dataclass(slots=True)
class ArticleView:
    id: int
    feed_name: str
    feed_url: str
    title: str
    author: str
    publish_time: datetime
    url: str
    full_content: str
    content_source: str
    capture_status: str
    summary: str
    summary_status: str
    summary_error: str


@dataclass(slots=True)
class DailyRunResult:
    run_key: str
    date: date
    collected: int = 0
    inserted: int = 0
    summarized: int = 0
    filtered_out: int = 0
    feed_errors: dict[str, str] = field(default_factory=dict)
    briefing_path: str = ""
