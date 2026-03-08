from datetime import date, datetime, timezone

import httpx

from gzhreader.config import FeedConfig, RSSConfig
from gzhreader.rss_client import RSSClient

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>新智元</title>
  <entry>
    <title>文章一</title>
    <link href="https://example.com/a1" />
    <updated>2026-03-07T02:30:00Z</updated>
    <author><name>新智元</name></author>
    <content type="html"><![CDATA[<p>这是第一篇正文</p>]]></content>
    <summary type="html"><![CDATA[<p>这是摘要</p>]]></summary>
  </entry>
</feed>
"""

ATOM_MANY = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>聚合源</title>
  <entry><title>1</title><link href="https://example.com/1" /><updated>2026-03-07T01:00:00Z</updated></entry>
  <entry><title>2</title><link href="https://example.com/2" /><updated>2026-03-07T02:00:00Z</updated></entry>
  <entry><title>3</title><link href="https://example.com/3" /><updated>2026-03-07T03:00:00Z</updated></entry>
</feed>
"""


def test_fetch_feed_parses_atom(monkeypatch) -> None:
    def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url, headers=headers)
        return httpx.Response(200, text=ATOM, request=request)

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    client = RSSClient(RSSConfig())
    items = client.fetch_feed(FeedConfig(name="聚合流", url="http://localhost/feed.atom"))

    assert len(items) == 1
    assert items[0].feed_name == "新智元"
    assert items[0].title == "文章一"
    assert items[0].content_text == "这是第一篇正文"


def test_fetch_feed_reads_all_source_entries_before_daily_limit(monkeypatch) -> None:
    def fake_get(self, url, headers=None):
        request = httpx.Request("GET", url, headers=headers)
        return httpx.Response(200, text=ATOM_MANY, request=request)

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    client = RSSClient(RSSConfig(daily_article_limit=1))
    items = client.fetch_feed(FeedConfig(name="聚合流", url="http://localhost/feed.atom"))

    assert len(items) == 3


def test_in_window_uses_local_timezone() -> None:
    client = RSSClient(RSSConfig(timezone="Asia/Shanghai", day_start="00:00"))
    published_at = datetime(2026, 3, 7, 2, 30, tzinfo=timezone.utc)
    assert client.in_window(published_at, date(2026, 3, 7)) is True
