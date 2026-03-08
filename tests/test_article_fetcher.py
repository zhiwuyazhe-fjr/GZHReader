from datetime import datetime

from gzhreader.article_fetcher import ArticleContentFetcher, FetchedArticleContent
from gzhreader.config import ArticleFetchConfig, RSSConfig
from gzhreader.types import FeedArticle


def make_fetcher() -> ArticleContentFetcher:
    return ArticleContentFetcher(ArticleFetchConfig(), RSSConfig())


def test_should_fetch_when_rss_content_is_missing() -> None:
    fetcher = make_fetcher()
    item = FeedArticle(
        feed_name="新智元",
        feed_url="http://localhost/feed.atom",
        title="标题",
        url="https://mp.weixin.qq.com/s/test",
        author="新智元",
        published_at=datetime(2026, 3, 7, 9, 0),
        content_html="",
        content_text="",
        summary_html="<p>摘要</p>",
        summary_text="摘要",
    )
    assert fetcher.should_fetch(item) is True


def test_extract_wechat_html_prefers_js_content() -> None:
    fetcher = make_fetcher()
    body = "".join(f"<p>这是第{i}段正文。</p>" for i in range(1, 80))
    html = f"""
    <html>
      <head><meta property="og:title" content="备用标题"></head>
      <body>
        <h1 id="activity-name">微信文章标题</h1>
        <a id="js_name">新智元</a>
        <em id="publish_time">2026-03-07 10:00</em>
        <div id="js_content">{body}</div>
      </body>
    </html>
    """

    result = fetcher._extract_from_html(
        html,
        "https://mp.weixin.qq.com/s/test",
        content_source="http_fulltext",
        fallback_title="回退标题",
        fallback_author="回退作者",
        fallback_publish_time=datetime(2026, 3, 7, 9, 0),
    )

    assert result.success is True
    assert result.title == "微信文章标题"
    assert result.author == "新智元"
    assert "这是第1段正文" in result.content_text


def test_extract_generic_html_uses_readability_fallback() -> None:
    fetcher = make_fetcher()
    paragraph = "通用网页正文内容。" * 80
    html = f"""
    <html>
      <head>
        <title>通用网页标题</title>
        <meta name="author" content="APPSO">
      </head>
      <body>
        <article>
          <h1>通用网页标题</h1>
          <p>{paragraph}</p>
        </article>
      </body>
    </html>
    """

    result = fetcher._extract_from_html(
        html,
        "https://example.com/post/1",
        content_source="http_fulltext",
        fallback_title="回退标题",
        fallback_author="回退作者",
        fallback_publish_time=datetime(2026, 3, 7, 9, 0),
    )

    assert result.success is True
    assert result.title == "通用网页标题"
    assert result.author == "APPSO"
    assert "通用网页正文内容" in result.content_text


def test_fetch_http_success_skips_browser() -> None:
    class StubFetcher(ArticleContentFetcher):
        def __init__(self):
            super().__init__(ArticleFetchConfig(), RSSConfig())
            self.browser_called = False

        def _fetch_via_http(self, *args, **kwargs):
            return FetchedArticleContent(
                title="文章",
                author="作者",
                publish_time=datetime(2026, 3, 7, 9, 0),
                content_text="正文内容" * 100,
                raw_html="<html></html>",
                content_source="http_fulltext",
                capture_status="http_fulltext",
            )

        def _fetch_via_browser(self, *args, **kwargs):
            self.browser_called = True
            raise AssertionError("browser fallback should not run")

    fetcher = StubFetcher()
    result = fetcher.fetch("https://example.com/post/1", fallback_title="标题")

    assert result.success is True
    assert fetcher.browser_called is False


def test_fetch_http_failure_falls_back_to_browser() -> None:
    class StubFetcher(ArticleContentFetcher):
        def __init__(self):
            super().__init__(ArticleFetchConfig(), RSSConfig())
            self.browser_called = False

        def _fetch_via_http(self, *args, **kwargs):
            return FetchedArticleContent(
                title="标题",
                author="作者",
                publish_time=datetime(2026, 3, 7, 9, 0),
                content_text="",
                raw_html="",
                content_source="fetch_failed",
                capture_status="fetch_failed",
                fetch_error="http failed",
            )

        def _fetch_via_browser(self, *args, **kwargs):
            self.browser_called = True
            return FetchedArticleContent(
                title="标题",
                author="作者",
                publish_time=datetime(2026, 3, 7, 9, 0),
                content_text="浏览器正文" * 100,
                raw_html="<html></html>",
                content_source="browser_dom",
                capture_status="browser_dom",
            )

    fetcher = StubFetcher()
    result = fetcher.fetch("https://example.com/post/1", fallback_title="标题")

    assert fetcher.browser_called is True
    assert result.content_source == "browser_dom"
    assert result.success is True
