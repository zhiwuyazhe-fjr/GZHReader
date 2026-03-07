from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import feedparser
import httpx
from bs4 import BeautifulSoup

from .config import FeedConfig, RSSConfig
from .types import FeedArticle

logger = logging.getLogger(__name__)


class RSSClient:
    def __init__(self, config: RSSConfig) -> None:
        self.config = config
        self.timezone = self._load_timezone(config.timezone)

    def fetch_feed(self, feed: FeedConfig) -> list[FeedArticle]:
        headers = {"User-Agent": self.config.user_agent}
        with httpx.Client(timeout=self.config.request_timeout_seconds, follow_redirects=True) as client:
            response = client.get(feed.url, headers=headers)
            response.raise_for_status()
        parsed = feedparser.parse(response.text)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise RuntimeError(f"RSS 解析失败: {getattr(parsed, 'bozo_exception', 'unknown error')}")

        default_name = feed.name or parsed.feed.get("title") or "Unnamed Feed"
        articles: list[FeedArticle] = []
        for entry in list(parsed.entries)[: self.config.max_articles_per_feed]:
            published_at = self._parse_entry_datetime(entry)
            content_html = self._pick_content_html(entry)
            summary_html = self._pick_summary_html(entry)
            content_text = self.html_to_text(content_html)
            summary_text = self.html_to_text(summary_html)
            link = str(entry.get("link", "")).strip()
            if not link and entry.get("links"):
                links = entry.get("links")
                if isinstance(links, list) and links:
                    link = str(links[0].get("href", "")).strip()
            author = str(entry.get("author", "")).strip() or (feed.author or default_name)
            logical_feed_name = author or default_name
            articles.append(
                FeedArticle(
                    feed_name=logical_feed_name,
                    feed_url=feed.url,
                    title=str(entry.get("title", "Untitled Article")).strip() or "Untitled Article",
                    url=link,
                    author=author,
                    published_at=published_at,
                    content_html=content_html,
                    content_text=content_text,
                    summary_html=summary_html,
                    summary_text=summary_text,
                )
            )
        logger.info("RSS: fetched %s entries from %s", len(articles), default_name)
        return articles

    def feed_window(self, target_date: date) -> tuple[datetime, datetime]:
        hour, minute = (int(part) for part in self.config.day_start.split(":"))
        start = datetime.combine(target_date, time(hour=hour, minute=minute), tzinfo=self.timezone)
        end = start + timedelta(days=1)
        return start, end

    def in_window(self, published_at: datetime, target_date: date) -> bool:
        start, end = self.feed_window(target_date)
        localized = published_at.astimezone(self.timezone) if published_at.tzinfo else published_at.replace(tzinfo=self.timezone)
        return start <= localized < end

    def check_feed(self, feed: FeedConfig) -> tuple[bool, str]:
        if not feed.url:
            return False, "缺少 RSS / Atom 链接"
        try:
            entries = self.fetch_feed(feed)
        except Exception as exc:
            return False, f"读取失败: {exc}"
        return True, f"可读取，最近返回 {len(entries)} 条"

    @staticmethod
    def html_to_text(html: str) -> str:
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    def _load_timezone(self, key: str):
        try:
            return ZoneInfo(key)
        except ZoneInfoNotFoundError:
            if key == "Asia/Shanghai":
                return timezone(timedelta(hours=8), name="Asia/Shanghai")
            return timezone.utc

    def _pick_content_html(self, entry) -> str:
        contents = entry.get("content") or []
        if contents:
            first = contents[0]
            if isinstance(first, dict):
                return str(first.get("value", ""))
            return str(first)
        return ""

    def _pick_summary_html(self, entry) -> str:
        for key in ("summary", "description"):
            if entry.get(key):
                return str(entry.get(key))
        return ""

    def _parse_entry_datetime(self, entry) -> datetime:
        for key in ("published_parsed", "updated_parsed"):
            value = entry.get(key)
            if value:
                return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)

        for key in ("published", "updated", "pubDate"):
            raw = entry.get(key)
            if not raw:
                continue
            try:
                parsed = parsedate_to_datetime(str(raw))
            except (TypeError, ValueError):
                try:
                    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except ValueError:
                    continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=self.timezone)
            return parsed.astimezone(timezone.utc)

        return datetime.now(timezone.utc)
