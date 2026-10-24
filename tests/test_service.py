from __future__ import annotations

from datetime import datetime, timezone
import threading
from types import SimpleNamespace

from gzhreader_core.models import ArticleRecord, ProviderBatch, SourceProfile
from gzhreader_core.service import MAX_CONTENT_REQUESTS_PER_SYNC, MAX_SUMMARIES_PER_SYNC, ReaderService
from gzhreader_core.storage import Storage


class Provider:
    def list_articles(self, source, **kwargs):
        item = ArticleRecord(source.id, "r1", "新文章", "https://mp.weixin.qq.com/s/r1", datetime.now(timezone.utc), source.name)
        return ProviderBatch([item], int(item.published_at.timestamp()), True, 1)
    def fetch_content(self, review_id): return "完整正文"


class Resolver: pass
class Fetcher: pass
class Summarizer:
    def summarize(self, title, content, source): return {"summary": "摘要", "key_points": [], "tags": [], "takeaway": "结论", "status": "done"}


def test_sync_deduplicates_and_updates_cursor(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    events = []
    service = ReaderService(storage, Provider(), Resolver(), Fetcher(), Summarizer(), sleep=lambda _: None)
    first = service.sync(lambda event, payload: events.append((event, payload)), force=True)
    second = service.sync(lambda event, payload: None, force=True)
    assert first["inserted"] == 1
    assert first["summarized"] == 1
    assert second["inserted"] == 0
    assert storage.source("MP_WXS_1")["sync_cursor"] > 0
    assert storage.article(1)["content"] == "完整正文"
    assert [event for event, _payload in events][-2:] == ["sync.completed", "articles.new_batch"]


def test_sync_releases_lock_when_start_job_fails(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "data.db")
    service = ReaderService(storage, Provider(), Resolver(), Fetcher(), Summarizer())
    start_job = storage.start_job
    monkeypatch.setattr(storage, "start_job", lambda *_: (_ for _ in ()).throw(OSError("disk unavailable")))
    assert service.sync(lambda *_: None)["errors"]
    assert not service.is_syncing
    monkeypatch.setattr(storage, "start_job", start_job)
    assert service.sync(lambda *_: None)["errors"] == []


def test_completed_event_observes_idle_sync_state(tmp_path):
    storage = Storage(tmp_path / "data.db")
    service = ReaderService(storage, Provider(), Resolver(), Fetcher(), Summarizer())
    observed = []
    service.sync(lambda event, _: observed.append(service.is_syncing) if event == "sync.completed" else None)
    assert observed == [False]


def test_prefilled_content_and_historical_articles_get_missing_summaries(tmp_path):
    storage = Storage(tmp_path / "data.db")
    source = SourceProfile("MP_WXS_1", "公众号")
    storage.upsert_source(source)
    item = ArticleRecord(source.id, "r1", "标题", "https://example.test/1", datetime.now(timezone.utc), content="完整正文")

    class PrefilledProvider:
        articles = [item]

        def list_articles(self, source, **kwargs):
            return ProviderBatch(self.articles, 123)

        def fetch_content(self, *_):
            raise AssertionError("already fetched content must be reused")

    provider = PrefilledProvider()
    service = ReaderService(storage, provider, Resolver(), Fetcher(), Summarizer(), sleep=lambda _: None)
    storage.update_settings({"auto_summary": False})
    assert service.sync(lambda *_: None, force=True)["summarized"] == 0
    assert storage.article(1)["summary_status"] == "pending"
    storage.update_settings({"auto_summary": True})
    provider.articles = []
    assert service.sync(lambda *_: None, force=True)["summarized"] == 1
    assert storage.article(1)["summary_status"] == "done"


def test_summary_failure_does_not_block_sync_and_is_retried(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))

    class FailingSummarizer:
        def summarize(self, *_):
            raise RuntimeError("AI unavailable")

    service = ReaderService(storage, Provider(), Resolver(), Fetcher(), FailingSummarizer(), sleep=lambda _: None)
    first = service.sync(lambda *_: None, force=True)
    assert first["errors"]
    assert storage.source("MP_WXS_1")["sync_cursor"] > 0
    assert storage.source("MP_WXS_1")["failure_count"] == 0
    assert storage.article(1)["content_status"] == "done"
    service.summarizer = Summarizer()
    assert service.sync(lambda *_: None, force=True)["summarized"] == 1


def test_summary_backlog_is_bounded_per_sync(tmp_path):
    storage = Storage(tmp_path / "data.db")
    source = SourceProfile("MP_WXS_1", "公众号")
    storage.upsert_source(source)
    storage.source_sync_success(source.id, 123)
    for index in range(MAX_SUMMARIES_PER_SYNC + 4):
        storage.insert_article(ArticleRecord(source.id, str(index), "标题", f"https://example.test/{index}", datetime.now(timezone.utc), content="正文"))

    class EmptyProvider:
        def list_articles(self, *_args, **_kwargs):
            return ProviderBatch([], 123)

    service = ReaderService(storage, EmptyProvider(), Resolver(), Fetcher(), Summarizer(), sleep=lambda _: None)
    assert service.sync(lambda *_: None, force=True)["summarized"] == MAX_SUMMARIES_PER_SYNC
    assert service.sync(lambda *_: None, force=True)["summarized"] == 4


def test_background_sync_rejects_duplicates_and_stops_before_new_work(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    entered, release = threading.Event(), threading.Event()

    class BlockingProvider(Provider):
        def list_articles(self, source, **kwargs):
            entered.set()
            assert release.wait(2)
            return super().list_articles(source, **kwargs)

    service = ReaderService(storage, BlockingProvider(), Resolver(), Fetcher(), Summarizer())
    try:
        assert service.start_sync(lambda *_: None, force=True)["accepted"] is True
        assert entered.wait(2)
        assert service.is_syncing
        assert service.start_sync(lambda *_: None)["accepted"] is False
        service.stop(timeout=0)
    finally:
        release.set()
        service.stop()
    assert not service.is_syncing
    assert storage.articles() == []
    assert service.start_sync(lambda *_: None)["accepted"] is False


def test_successful_fallback_content_clears_previous_fetch_error(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))

    class ContentFailureProvider(Provider):
        def fetch_content(self, *_):
            raise RuntimeError("provider unavailable")

    class SuccessfulFetcher:
        def fetch(self, *_args, **_kwargs):
            return SimpleNamespace(content_text="网页完整正文", fetch_error="")

    service = ReaderService(storage, ContentFailureProvider(), Resolver(), SuccessfulFetcher(), Summarizer())
    service.sync(lambda *_: None, force=True)
    assert storage.article(1)["content"] == "网页完整正文"
    assert storage.article(1)["content_error"] == ""


def test_event_delivery_failure_does_not_fail_persisted_sync(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    service = ReaderService(storage, Provider(), Resolver(), Fetcher(), Summarizer())

    def disconnected_listener(*_):
        raise BrokenPipeError()

    result = service.sync(disconnected_listener, force=True)
    assert result["inserted"] == 1
    assert not result["errors"]
    assert not service.is_syncing


def test_large_batch_saves_all_metadata_and_bounds_content_requests(tmp_path):
    storage = Storage(tmp_path / "data.db")
    source = SourceProfile("MP_WXS_1", "公众号")
    storage.upsert_source(source)

    class LargeProvider(Provider):
        content_calls = 0

        def list_articles(self, source, **kwargs):
            articles = [
                ArticleRecord(source.id, str(index), "标题", f"https://example.test/{index}", datetime.now(timezone.utc))
                for index in range(MAX_CONTENT_REQUESTS_PER_SYNC + 5)
            ]
            return ProviderBatch(articles, 456)

        def fetch_content(self, *_):
            self.content_calls += 1
            return "正文"

    provider = LargeProvider()
    service = ReaderService(storage, provider, Resolver(), Fetcher(), Summarizer(), sleep=lambda _: None)
    first = service.sync(lambda *_: None, force=True)
    assert first["inserted"] == MAX_CONTENT_REQUESTS_PER_SYNC + 5
    assert provider.content_calls == MAX_CONTENT_REQUESTS_PER_SYNC
    assert first["content_pending"] == 5
    assert storage.source(source.id)["sync_cursor"] == 456
    assert len(storage.pending_content()) == 5
    assert service.sync(lambda *_: None, force=True)["inserted"] == 0
    assert len(storage.pending_content()) == 0


def test_shutdown_during_provider_content_does_not_launch_fallback(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    entered, release = threading.Event(), threading.Event()

    class SlowEmptyProvider(Provider):
        def fetch_content(self, *_):
            entered.set()
            assert release.wait(5)
            return ""

    class ForbiddenFetcher:
        calls = 0

        def fetch(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("fallback must not start after shutdown")

    fetcher = ForbiddenFetcher()
    service = ReaderService(storage, SlowEmptyProvider(), Resolver(), fetcher, Summarizer())
    try:
        assert service.start_sync(lambda *_: None, force=True)["accepted"]
        assert entered.wait(5)
        service.stop(timeout=0)
    finally:
        release.set()
        service.stop()
    assert fetcher.calls == 0
    assert storage.article(1)["content_status"] == "pending"
