from datetime import datetime

from gzhreader.article_fetcher import FetchedArticleContent
from gzhreader.briefing import BriefingBuilder
from gzhreader.config import AppConfig, FeedConfig, LLMConfig, OutputConfig, RSSConfig
from gzhreader.service import ReaderService
from gzhreader.storage import Storage
from gzhreader.summarizer import OpenAICompatibleSummarizer
from gzhreader.types import ArticleDraft, FeedArticle


class FakeRSSClient:
    def __init__(self, articles):
        self.articles = articles

    def fetch_feed(self, feed):
        return self.articles

    def in_window(self, published_at, target_date):
        return True


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


def make_config(tmp_path):
    return AppConfig(
        db_path=str(tmp_path / "app.db"),
        feeds=[FeedConfig(name="新智元", url="http://localhost/feed.atom", active=True, order=1)],
        rss=RSSConfig(),
        llm=LLMConfig(api_key=""),
        output=OutputConfig(briefing_dir=str(tmp_path / "briefings"), raw_archive_dir=str(tmp_path / "raw")),
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


def test_service_enhances_existing_weak_article_without_duplicate(tmp_path) -> None:
    config = make_config(tmp_path)
    storage = Storage(config.db_path)
    storage.init_db()
    storage.upsert_feeds(config.feeds)

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
    storage.upsert_feeds(config.feeds)

    storage.insert_article_if_new(
        ArticleDraft(
            feed_name="???",
            feed_url="http://localhost/feed.atom",
            title="??1",
            author="???",
            publish_time=datetime(2026, 3, 7, 8, 0),
            url="https://example.com/a1",
            full_content="???",
            raw_html="<p>???</p>",
            content_source="title_only",
            capture_status="rss_empty",
            fingerprint="seed-fingerprint",
            summary="???",
            summary_status="done",
        ),
        run_key="seed",
    )

    weak_article = FeedArticle(
        feed_name="???",
        feed_url="http://localhost/feed.atom",
        title="??1",
        url="https://example.com/a1",
        author="???",
        published_at=datetime(2026, 3, 7, 8, 0),
        content_html="",
        content_text="",
        summary_html="<p>???</p>",
        summary_text="???",
    )
    service = ReaderService(
        config,
        storage,
        FakeRSSClient([weak_article]),
        FakeSummarizer(),
        BriefingBuilder(),
        article_fetcher=FakeFetcher(
            FetchedArticleContent(
                title="??1",
                author="???",
                publish_time=datetime(2026, 3, 7, 8, 0),
                content_text="??????" * 100,
                raw_html="<article>??????</article>",
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
    assert "??????" in views[0].full_content

