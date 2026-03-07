from datetime import datetime

from gzhreader.briefing import BriefingBuilder
from gzhreader.config import AppConfig, FeedConfig, LLMConfig, OutputConfig, RSSConfig
from gzhreader.service import ReaderService
from gzhreader.storage import Storage
from gzhreader.summarizer import OpenAICompatibleSummarizer
from gzhreader.types import FeedArticle


class FakeRSSClient:
    def fetch_feed(self, feed):
        return [
            FeedArticle(
                feed_name=feed.name,
                feed_url=feed.url,
                title="文章1",
                url="https://example.com/a1",
                author="新智元",
                published_at=datetime(2026, 3, 7, 8, 0),
                content_html="<p>正文测试内容</p>",
                content_text="正文测试内容",
                summary_html="",
                summary_text="",
            )
        ]

    def in_window(self, published_at, target_date):
        return True


class FakeSummarizer(OpenAICompatibleSummarizer):
    def __init__(self):
        pass

    def summarize(self, item):
        return f"摘要:{item.title}"


def test_service_run_generates_single_briefing(tmp_path) -> None:
    config = AppConfig(
        db_path=str(tmp_path / "app.db"),
        feeds=[FeedConfig(name="新智元", url="http://localhost/feed.atom", active=True, order=1)],
        rss=RSSConfig(),
        llm=LLMConfig(api_key=""),
        output=OutputConfig(briefing_dir=str(tmp_path / "briefings"), raw_archive_dir=str(tmp_path / "raw")),
    )
    storage = Storage(config.db_path)
    service = ReaderService(config, storage, FakeRSSClient(), FakeSummarizer(), BriefingBuilder())

    result = service.run_for_date(datetime(2026, 3, 7).date())

    assert result.inserted == 1
    assert result.summarized == 1
    assert (tmp_path / "briefings" / "2026-03-07.md").exists()
