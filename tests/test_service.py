from datetime import date, datetime, timezone

from gzhreader.article_fetcher import FetchedArticleContent
from gzhreader.briefing import BriefingBuilder
from gzhreader.config import AppConfig, LLMConfig, OutputConfig, RSSConfig, SourceConfig
from gzhreader.service import ReaderService
from gzhreader.storage import Storage
from gzhreader.summarizer import OpenAICompatibleSummarizer
from gzhreader.types import ArticleDraft, FeedArticle


class FakeRSSClient:
    def __init__(self, articles, in_window_fn=None):
        self.articles = articles
        self.in_window_fn = in_window_fn or (lambda published_at, target_date: True)

    def fetch_feed(self, feed):
        return self.articles

    def in_window(self, published_at, target_date):
        return self.in_window_fn(published_at, target_date)


class FakeSummarizer(OpenAICompatibleSummarizer):
    def __init__(self):
        pass

    def summarize(self, item):
        return f"摘要:{item.title}"


class FakeFetcher:
    def __init__(self, fetched: FetchedArticleContent, should_fetch: bool = True):
        self.fetched = fetched
        self.should_fetch_value = should_fetch
        self.calls = 0

    def should_fetch(self, item):
        return self.should_fetch_value

    def fetch(self, url, **kwargs):
        self.calls += 1
        return self.fetched


def make_config(tmp_path, *, daily_article_limit: str | int = 20):
    return AppConfig(
        db_path=str(tmp_path / "app.db"),
        source=SourceConfig(url="http://localhost/feed.atom"),
        rss=RSSConfig(daily_article_limit=daily_article_limit),
        llm=LLMConfig(api_key=""),
        output=OutputConfig(
            briefing_dir=str(tmp_path / "briefings"),
            raw_archive_dir=str(tmp_path / "raw"),
            save_raw_html=False,
        ),
    )


def make_article(title: str, published_at: datetime, url_suffix: str) -> FeedArticle:
    return FeedArticle(
        feed_name="新智元",
        feed_url="http://localhost/feed.atom",
        title=title,
        url=f"https://example.com/{url_suffix}",
        author="新智元",
        published_at=published_at,
        content_html="<p>RSS 正文</p>",
        content_text="RSS 正文",
        summary_html="<p>摘要</p>",
        summary_text="摘要",
    )


def make_unused_fetcher() -> FakeFetcher:
    return FakeFetcher(
        FetchedArticleContent(
            title="unused",
            author="unused",
            publish_time=datetime(2026, 3, 7, 8, 0),
            content_text="unused content",
            raw_html="<article>unused</article>",
            content_source="http_fulltext",
            capture_status="http_fulltext",
        ),
        should_fetch=False,
    )


def test_service_fetches_fulltext_for_summary_only_rss(tmp_path) -> None:
    config = make_config(tmp_path)
    storage = Storage(config.db_path)
    article = FeedArticle(
        feed_name="新智元",
        feed_url="http://localhost/feed.atom",
        title="文章1",
        url="https://example.com/a1",
        author="新智元",
        published_at=datetime(2026, 3, 7, 8, 0),
        content_html="",
        content_text="",
        summary_html="<p>短摘要</p>",
        summary_text="短摘要",
    )
    fetcher = FakeFetcher(
        FetchedArticleContent(
            title="文章1",
            author="新智元",
            publish_time=datetime(2026, 3, 7, 8, 0),
            content_text="增强正文" * 100,
            raw_html="<article>增强正文</article>",
            content_source="http_fulltext",
            capture_status="http_fulltext",
        )
    )
    service = ReaderService(
        config,
        storage,
        FakeRSSClient([article]),
        FakeSummarizer(),
        BriefingBuilder(),
        article_fetcher=fetcher,
    )

    result = service.run_for_date(datetime(2026, 3, 7).date())
    views = storage.get_article_views_for_date(datetime(2026, 3, 7).date())

    assert fetcher.calls == 1
    assert result.inserted == 1
    assert result.summarized == 1
    assert views[0].content_source == "http_fulltext"
    assert "增强正文" in views[0].full_content
    assert (tmp_path / "briefings" / "2026-03-07.md").exists()
    assert not (tmp_path / "raw").exists()


def test_service_applies_daily_limit_after_date_filter(tmp_path) -> None:
    target_date = date(2026, 3, 7)
    config = make_config(tmp_path, daily_article_limit=2)
    storage = Storage(config.db_path)
    articles = [
        make_article("前一天-1", datetime(2026, 3, 6, 8, 0), "old-1"),
        make_article("前一天-2", datetime(2026, 3, 6, 9, 0), "old-2"),
        make_article("当天-1", datetime(2026, 3, 7, 8, 0), "today-1"),
        make_article("当天-2", datetime(2026, 3, 7, 9, 0), "today-2"),
        make_article("当天-3", datetime(2026, 3, 7, 10, 0), "today-3"),
    ]
    service = ReaderService(
        config,
        storage,
        FakeRSSClient(articles, in_window_fn=lambda published_at, day: published_at.date() == day),
        FakeSummarizer(),
        BriefingBuilder(),
        article_fetcher=make_unused_fetcher(),
    )

    result = service.run_for_date(target_date)
    views = storage.get_article_views_for_date(target_date)

    assert result.collected == 5
    assert result.inserted == 2
    assert result.filtered_out == 3
    assert [view.title for view in views] == ["当天-1", "当天-2"]


def test_service_uses_all_for_all_matching_articles(tmp_path) -> None:
    target_date = date(2026, 3, 7)
    config = make_config(tmp_path, daily_article_limit="all")
    storage = Storage(config.db_path)
    articles = [
        make_article("前一天", datetime(2026, 3, 6, 8, 0), "old"),
        make_article("当天-1", datetime(2026, 3, 7, 8, 0), "today-1"),
        make_article("当天-2", datetime(2026, 3, 7, 9, 0), "today-2"),
        make_article("当天-3", datetime(2026, 3, 7, 10, 0), "today-3"),
    ]
    service = ReaderService(
        config,
        storage,
        FakeRSSClient(articles, in_window_fn=lambda published_at, day: published_at.date() == day),
        FakeSummarizer(),
        BriefingBuilder(),
        article_fetcher=make_unused_fetcher(),
    )

    result = service.run_for_date(target_date)
    views = storage.get_article_views_for_date(target_date)

    assert result.collected == 4
    assert result.inserted == 3
    assert result.filtered_out == 1
    assert len(views) == 3


def test_service_enhances_existing_weak_article_without_duplicate(tmp_path) -> None:
    config = make_config(tmp_path)
    storage = Storage(config.db_path)
    storage.init_db()
    storage.upsert_feeds(config.runtime_feeds())

    storage.insert_article_if_new(
        ArticleDraft(
            feed_name="新智元",
            feed_url="http://localhost/feed.atom",
            title="文章1",
            author="新智元",
            publish_time=datetime(2026, 3, 7, 8, 0),
            url="https://example.com/a1",
            full_content="短摘要",
            raw_html="<p>短摘要</p>",
            content_source="rss_summary",
            capture_status="rss_summary_only",
            fingerprint="weak-fingerprint",
        ),
        run_key="seed",
    )

    weak_article = FeedArticle(
        feed_name="新智元",
        feed_url="http://localhost/feed.atom",
        title="文章1",
        url="https://example.com/a1",
        author="新智元",
        published_at=datetime(2026, 3, 7, 8, 0),
        content_html="",
        content_text="",
        summary_html="<p>短摘要</p>",
        summary_text="短摘要",
    )
    service = ReaderService(
        config,
        storage,
        FakeRSSClient([weak_article]),
        FakeSummarizer(),
        BriefingBuilder(),
        article_fetcher=FakeFetcher(
            FetchedArticleContent(
                title="文章1",
                author="新智元",
                publish_time=datetime(2026, 3, 7, 8, 0),
                content_text="浏览器增强正文" * 100,
                raw_html="<article>浏览器增强正文</article>",
                content_source="browser_dom",
                capture_status="browser_dom",
            )
        ),
    )
    result = service.run_for_date(datetime(2026, 3, 7).date())
    views = storage.get_article_views_for_date(datetime(2026, 3, 7).date())

    assert result.inserted == 0
    assert result.summarized == 1
    assert len(views) == 1
    assert views[0].content_source == "browser_dom"
    assert "浏览器增强正文" in views[0].full_content


def test_service_enhances_already_summarized_weak_article(tmp_path) -> None:
    config = make_config(tmp_path)
    storage = Storage(config.db_path)
    storage.init_db()
    storage.upsert_feeds(config.runtime_feeds())

    storage.insert_article_if_new(
        ArticleDraft(
            feed_name="新智元",
            feed_url="http://localhost/feed.atom",
            title="文章1",
            author="新智元",
            publish_time=datetime(2026, 3, 7, 8, 0),
            url="https://example.com/a1",
            full_content="旧标题摘要",
            raw_html="<p>旧标题摘要</p>",
            content_source="title_only",
            capture_status="rss_empty",
            fingerprint="seed-fingerprint",
            summary="旧摘要",
            summary_status="done",
        ),
        run_key="seed",
    )

    weak_article = FeedArticle(
        feed_name="新智元",
        feed_url="http://localhost/feed.atom",
        title="文章1",
        url="https://example.com/a1",
        author="新智元",
        published_at=datetime(2026, 3, 7, 8, 0),
        content_html="",
        content_text="",
        summary_html="<p>旧标题摘要</p>",
        summary_text="旧标题摘要",
    )
    service = ReaderService(
        config,
        storage,
        FakeRSSClient([weak_article]),
        FakeSummarizer(),
        BriefingBuilder(),
        article_fetcher=FakeFetcher(
            FetchedArticleContent(
                title="文章1",
                author="新智元",
                publish_time=datetime(2026, 3, 7, 8, 0),
                content_text="新的原文正文" * 100,
                raw_html="<article>新的原文正文</article>",
                content_source="browser_dom",
                capture_status="browser_dom",
            )
        ),
    )

    result = service.run_for_date(datetime(2026, 3, 7).date())
    views = storage.get_article_views_for_date(datetime(2026, 3, 7).date())

    assert result.inserted == 0
    assert result.summarized == 1
    assert len(views) == 1
    assert views[0].content_source == "browser_dom"
    assert "新的原文正文" in views[0].full_content


def test_service_keeps_cross_utc_articles_in_same_local_day(tmp_path) -> None:
    target_date = date(2026, 3, 8)
    config = make_config(tmp_path, daily_article_limit='all')
    storage = Storage(config.db_path)
    articles = [
        make_article('today-early', datetime(2026, 3, 8, 7, 4, 46, tzinfo=timezone.utc), 'today-early'),
        make_article('today-cross-utc', datetime(2026, 3, 7, 22, 4, 48, tzinfo=timezone.utc), 'today-cross-utc'),
    ]
    service = ReaderService(
        config,
        storage,
        FakeRSSClient(articles, in_window_fn=lambda published_at, day: True),
        FakeSummarizer(),
        BriefingBuilder(),
        article_fetcher=make_unused_fetcher(),
    )

    result = service.run_for_date(target_date)
    markdown = (tmp_path / 'briefings' / '2026-03-08.md').read_text(encoding='utf-8')

    assert result.inserted == 2
    assert result.summarized == 2
    assert '- \u6587\u7ae0\u603b\u6570\uff1a2' in markdown
    assert 'today-early' in markdown
    assert 'today-cross-utc' in markdown
