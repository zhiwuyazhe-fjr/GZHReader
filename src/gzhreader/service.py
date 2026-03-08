from __future__ import annotations

import logging
from datetime import date, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .article_fetcher import ArticleContentFetcher, FetchedArticleContent
from .briefing import BriefingBuilder
from .config import AppConfig, describe_daily_article_limit
from .rss_client import RSSClient
from .storage import Storage, build_fingerprint
from .summarizer import OpenAICompatibleSummarizer, SummarizeInput
from .types import ArticleDraft, ArticleView, DailyRunResult, FeedArticle, StoredArticle

logger = logging.getLogger(__name__)

WEAK_CONTENT_SOURCES = {"rss_summary", "title_only"}
WEAK_CAPTURE_STATUSES = {"rss_empty", "rss_summary_only", "fetch_failed"}
STRONG_CONTENT_SOURCES = {"rss_content", "http_fulltext", "browser_dom"}


class ReaderService:
    def __init__(
        self,
        config: AppConfig,
        storage: Storage,
        rss_client: RSSClient,
        summarizer: OpenAICompatibleSummarizer,
        briefing_builder: BriefingBuilder,
        article_fetcher: ArticleContentFetcher | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.rss_client = rss_client
        self.summarizer = summarizer
        self.briefing_builder = briefing_builder
        self.article_fetcher = article_fetcher or ArticleContentFetcher(config.article_fetch, config.rss)

    def run_for_date(self, target_date: date, feed_filter: str | None = None) -> DailyRunResult:
        import uuid

        run_key = uuid.uuid4().hex[:8]
        result = DailyRunResult(run_key=run_key, date=target_date)
        self.storage.init_db()
        self.storage.upsert_feeds(self.config.runtime_feeds())
        self.storage.start_run(run_key, target_date)
        feeds = self.storage.list_feeds(active_only=True, name=feed_filter)

        for feed in feeds:
            try:
                collected = self.rss_client.fetch_feed(feed)
                result.collected += len(collected)

                daily_articles = [item for item in collected if self.rss_client.in_window(item.published_at, target_date)]
                selected_articles = self._apply_daily_article_limit(daily_articles)
                result.filtered_out += len(collected) - len(selected_articles)
                logger.info(
                    "RSS: selected %s articles for %s from %s (source_returned=%s, date_matched=%s, limit=%s)",
                    len(selected_articles),
                    target_date.isoformat(),
                    feed.name,
                    len(collected),
                    len(daily_articles),
                    describe_daily_article_limit(self.config.rss.daily_article_limit),
                )

                for item in selected_articles:
                    draft = self._build_draft(item)
                    existing = self.storage.find_article(draft.url, draft.fingerprint)
                    if existing is None:
                        inserted = self.storage.insert_article_if_new(draft, run_key=run_key)
                        if inserted:
                            result.inserted += 1
                            self._archive_raw_html(target_date, draft)
                        continue

                    if self._should_enhance(existing, draft, target_date):
                        logger.info("Content fetch: enhancing article %s with %s", draft.title, draft.content_source)
                        self.storage.enhance_article(existing.id, draft, run_key=run_key)
                        self._archive_raw_html(target_date, draft)
            except Exception as exc:
                result.feed_errors[feed.name] = str(exc)

        for article in self._pending_articles_for_target_date(target_date):
            try:
                content = article.full_content or article.title
                summary = self.summarizer.summarize(
                    SummarizeInput(title=article.title, content=content, author=article.author)
                )
                self.storage.mark_article_summarized(article.id, summary)
                result.summarized += 1
            except Exception as exc:
                self.storage.mark_article_summary_failed(article.id, str(exc))

        views = self._article_views_for_target_date(target_date)
        markdown = self.briefing_builder.build(target_date, views)
        self.storage.save_briefing(target_date, markdown)

        output_dir = Path(self.config.output.briefing_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        briefing_path = output_dir / f"{target_date.isoformat()}.md"
        briefing_path.write_text(markdown, encoding="utf-8")
        result.briefing_path = str(briefing_path)

        self.storage.finish_run(
            run_key,
            status="done" if not result.feed_errors else "partial",
            collected=result.collected,
            inserted=result.inserted,
            summarized=result.summarized,
            error_count=len(result.feed_errors),
            briefing_path=result.briefing_path,
            notes="; ".join(f"{key}: {value}" for key, value in result.feed_errors.items()),
        )
        return result

    def _apply_daily_article_limit(self, items: list[FeedArticle]) -> list[FeedArticle]:
        limit = self.config.rss.daily_article_limit
        if limit == "all":
            return list(items)
        return list(items)[:limit]

    def _build_draft(self, item: FeedArticle) -> ArticleDraft:
        base_draft = self._build_draft_from_rss(item)
        if not self.article_fetcher.should_fetch(item):
            logger.info("Content fetch: RSS fulltext hit for %s", item.title)
            return base_draft

        logger.info("Content fetch: RSS content weak, fetching article %s", item.title)
        fetched = self.article_fetcher.fetch(
            item.url,
            fallback_title=item.title,
            fallback_author=item.author,
            fallback_publish_time=item.published_at,
        )
        if fetched.success:
            logger.info("Content fetch: final source %s for %s", fetched.content_source, item.title)
            return self._build_draft_from_fetched(item, fetched)

        logger.info(
            "Content fetch: fallback to RSS source %s for %s (%s)",
            base_draft.content_source,
            item.title,
            fetched.fetch_error,
        )
        base_draft.capture_status = "fetch_failed"
        return base_draft

    def _build_draft_from_rss(self, item: FeedArticle) -> ArticleDraft:
        if item.content_text:
            full_content = item.content_text
            raw_html = item.content_html
            content_source = "rss_content"
            capture_status = "rss_fulltext"
        elif item.summary_text:
            full_content = item.summary_text
            raw_html = item.summary_html
            content_source = "rss_summary"
            capture_status = "rss_summary_only"
        else:
            full_content = item.title
            raw_html = item.summary_html or item.content_html
            content_source = "title_only"
            capture_status = "rss_empty"

        return ArticleDraft(
            feed_name=item.feed_name,
            feed_url=item.feed_url,
            title=item.title,
            author=item.author,
            publish_time=self._normalize_publish_time(item.published_at),
            url=item.url,
            full_content=full_content,
            raw_html=raw_html,
            content_source=content_source,
            capture_status=capture_status,
            fingerprint=build_fingerprint(
                item.feed_name,
                item.published_at.date(),
                item.title,
                item.url,
                full_content,
            ),
        )

    def _build_draft_from_fetched(self, item: FeedArticle, fetched: FetchedArticleContent) -> ArticleDraft:
        title = fetched.title or item.title
        author = fetched.author or item.author
        publish_time = self._normalize_publish_time(fetched.publish_time or item.published_at)
        full_content = fetched.content_text.strip() or item.summary_text.strip() or item.title
        return ArticleDraft(
            feed_name=item.feed_name,
            feed_url=item.feed_url,
            title=title,
            author=author,
            publish_time=publish_time,
            url=item.url,
            full_content=full_content,
            raw_html=fetched.raw_html,
            content_source=fetched.content_source,
            capture_status=fetched.capture_status,
            fingerprint=build_fingerprint(
                item.feed_name,
                publish_time.date(),
                title,
                item.url,
                full_content,
            ),
        )

    def _should_enhance(self, existing: StoredArticle, draft: ArticleDraft, target_date: date) -> bool:
        if not self.rss_client.in_window(existing.publish_time, target_date):
            return False
        if draft.content_source not in STRONG_CONTENT_SOURCES:
            return False
        existing_is_weak = (
            existing.content_source in WEAK_CONTENT_SOURCES or existing.capture_status in WEAK_CAPTURE_STATUSES
        )
        if not existing_is_weak:
            return False
        return len(draft.full_content.strip()) > len(existing.full_content.strip())

    def _pending_articles_for_target_date(self, target_date: date) -> list[StoredArticle]:
        return [
            article
            for article in self.storage.get_unsummarized_articles()
            if self.rss_client.in_window(article.publish_time, target_date)
        ]

    def _article_views_for_target_date(self, target_date: date) -> list[ArticleView]:
        return [
            view
            for view in self.storage.get_all_article_views()
            if self.rss_client.in_window(view.publish_time, target_date)
        ]

    def _normalize_publish_time(self, publish_time):
        if publish_time.tzinfo is None:
            timezone_obj = getattr(self.rss_client, "timezone", None)
            if timezone_obj is None:
                try:
                    timezone_obj = ZoneInfo(self.config.rss.timezone)
                except ZoneInfoNotFoundError:
                    if self.config.rss.timezone == "Asia/Shanghai":
                        timezone_obj = timezone(timedelta(hours=8), name="Asia/Shanghai")
                    else:
                        timezone_obj = timezone.utc
            publish_time = publish_time.replace(tzinfo=timezone_obj)
        return publish_time.astimezone(timezone.utc)

    def _archive_raw_html(self, target_date: date, draft: ArticleDraft) -> None:
        if not self.config.output.save_raw_html:
            return
        if not draft.raw_html:
            return
        archive_dir = Path(self.config.output.raw_archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(ch if ch.isalnum() else "-" for ch in draft.title)[:48].strip("-") or "article"
        file_path = archive_dir / f"{target_date.isoformat()}-{safe_name}-{draft.fingerprint[:8]}.html"
        if not file_path.exists():
            file_path.write_text(draft.raw_html, encoding="utf-8")
