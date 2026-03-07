from __future__ import annotations

from datetime import date
from pathlib import Path

from .briefing import BriefingBuilder
from .config import AppConfig
from .rss_client import RSSClient
from .storage import Storage, build_fingerprint
from .summarizer import OpenAICompatibleSummarizer, SummarizeInput
from .types import ArticleDraft, DailyRunResult, FeedArticle


class ReaderService:
    def __init__(
        self,
        config: AppConfig,
        storage: Storage,
        rss_client: RSSClient,
        summarizer: OpenAICompatibleSummarizer,
        briefing_builder: BriefingBuilder,
    ) -> None:
        self.config = config
        self.storage = storage
        self.rss_client = rss_client
        self.summarizer = summarizer
        self.briefing_builder = briefing_builder

    def run_for_date(self, target_date: date, feed_filter: str | None = None) -> DailyRunResult:
        import uuid

        run_key = uuid.uuid4().hex[:8]
        result = DailyRunResult(run_key=run_key, date=target_date)
        self.storage.init_db()
        self.storage.upsert_feeds(self.config.feeds)
        self.storage.start_run(run_key, target_date)
        feeds = self.storage.list_feeds(active_only=True, name=feed_filter)

        for feed in feeds:
            try:
                collected = self.rss_client.fetch_feed(feed)
                for item in collected:
                    result.collected += 1
                    if not self.rss_client.in_window(item.published_at, target_date):
                        result.filtered_out += 1
                        continue
                    draft = self._build_draft(item)
                    inserted = self.storage.insert_article_if_new(draft, run_key=run_key)
                    if inserted:
                        result.inserted += 1
                        self._archive_raw_html(target_date, draft)
            except Exception as exc:
                result.feed_errors[feed.name] = str(exc)

        for article in self.storage.get_unsummarized_for_date(target_date):
            try:
                content = article.full_content or article.title
                summary = self.summarizer.summarize(
                    SummarizeInput(title=article.title, content=content, author=article.author)
                )
                self.storage.mark_article_summarized(article.id, summary)
                result.summarized += 1
            except Exception as exc:
                self.storage.mark_article_summary_failed(article.id, str(exc))

        views = self.storage.get_article_views_for_date(target_date)
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

    def _build_draft(self, item: FeedArticle) -> ArticleDraft:
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
            publish_time=item.published_at,
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

    def _archive_raw_html(self, target_date: date, draft: ArticleDraft) -> None:
        if not draft.raw_html:
            return
        archive_dir = Path(self.config.output.raw_archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(ch if ch.isalnum() else "-" for ch in draft.title)[:48].strip("-") or "article"
        file_path = archive_dir / f"{target_date.isoformat()}-{safe_name}-{draft.fingerprint[:8]}.html"
        if not file_path.exists():
            file_path.write_text(draft.raw_html, encoding="utf-8")
