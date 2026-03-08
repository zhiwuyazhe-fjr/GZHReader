from datetime import datetime

from gzhreader.briefing import BriefingBuilder
from gzhreader.types import ArticleView


def test_briefing_grouped_by_feed_includes_failures_and_source_labels() -> None:
    builder = BriefingBuilder()
    markdown = builder.build(
        datetime(2026, 3, 7).date(),
        [
            ArticleView(
                id=1,
                feed_name="新智元",
                feed_url="http://localhost/feed.atom",
                title="文章1",
                author="作者A",
                publish_time=datetime(2026, 3, 7, 8, 0),
                url="https://example.com/a1",
                full_content="正文",
                content_source="http_fulltext",
                capture_status="http_fulltext",
                summary="摘要内容",
                summary_status="done",
                summary_error="",
            ),
            ArticleView(
                id=2,
                feed_name="新智元",
                feed_url="http://localhost/feed.atom",
                title="文章2",
                author="作者A",
                publish_time=datetime(2026, 3, 7, 9, 0),
                url="",
                full_content="",
                content_source="title_only",
                capture_status="fetch_failed",
                summary="",
                summary_status="failed",
                summary_error="摘要失败",
            ),
        ],
    )

    assert "## 新智元" in markdown
    assert "### 文章1" in markdown
    assert "正文来源：HTTP 抓取" in markdown
    assert "### 失败项" in markdown
    assert "文章2" in markdown
