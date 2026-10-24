from __future__ import annotations

import sqlite3
import zoneinfo
from datetime import date, datetime, timedelta, timezone

import pytest

from gzhreader_core.models import ArticleRecord, SourceProfile
from gzhreader_core.storage import Storage


def test_storage_search_unread_and_briefing(tmp_path):
    storage = Storage(tmp_path / "data.db", tmp_path / "backups")
    storage.upsert_source(SourceProfile("MP_WXS_1", "测试公众号"))
    article_id, inserted = storage.insert_article(
        ArticleRecord(
            source_id="MP_WXS_1",
            origin_id="review-1",
            title="一篇关于本地阅读的文章",
            url="https://mp.weixin.qq.com/s/demo",
            published_at=datetime.now(timezone.utc),
            content="正文讨论本地工作台和信息整理。",
        )
    )
    assert inserted
    storage.update_summary(article_id, {"summary": "本地阅读摘要", "key_points": ["本地保存"], "tags": ["阅读"], "takeaway": "减少噪音", "status": "done"})
    assert storage.articles(query="本地阅读")[0]["id"] == article_id
    assert storage.dashboard()["unread_count"] == 1
    storage.mark_read(article_id)
    assert storage.dashboard()["unread_count"] == 0
    storage.save_briefing(date.today(), "概览", "# 简报", "brief.md", 1)
    storage.save_briefing(date.today(), "新概览", "# 新简报", "brief.md", 1)
    assert storage.briefing(date.today())["overview"] == "新概览"
    assert list((tmp_path / "backups").glob("*.db"))


def test_settings_validation(tmp_path):
    storage = Storage(tmp_path / "data.db")
    assert storage.update_settings({"refresh_minutes": 15})["refresh_minutes"] == 15
    try:
        storage.update_settings({"refresh_minutes": 17})
    except ValueError as exc:
        assert "刷新频率" in str(exc)
    else:
        raise AssertionError("invalid refresh option accepted")


def test_connection_cooldown_round_trip(tmp_path):
    storage = Storage(tmp_path / "data.db")
    until = "2026-08-06T03:00:00+00:00"
    storage.set_connection_state("weread", "cooldown", "????", True, until)
    state = storage.connection_state("weread")
    assert state["state"] == "cooldown"
    assert state["reconnect_required"] == 1
    assert state["cooldown_until"] == until


def add_article(storage, origin_id="1", *, source_id="MP_WXS_1", title="本地阅读", content="正文整理", published_at=None):
    return storage.insert_article(ArticleRecord(
        source_id=source_id, origin_id=origin_id, title=title,
        url=f"https://mp.weixin.qq.com/s/{source_id}-{origin_id}",
        published_at=published_at or datetime.now(timezone.utc), content=content,
    ))[0]


def test_search_short_terms_filters_and_pagination(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "测试公众号"))
    first = add_article(storage, "1", title="本地阅读 100%", content="正文整理")
    second = add_article(storage, "2", title="本地阅读", content="正文整理")
    add_article(storage, "3", title="完全不同", content="杂谈")
    storage.mark_read(first)

    assert [item["id"] for item in storage.articles(query="本地 正文")] == [second, first]
    assert storage.articles(query="本地", unread_only=True)[0]["id"] == second
    assert storage.article_count(query="本地") == 2
    assert [item["id"] for item in storage.articles(limit=1, offset=1)] == [second]
    assert [item["id"] for item in storage.articles(query="%")] == [first]
    assert storage.articles(limit=0) == []
    assert storage.article_count() == 3


def test_search_without_fts_uses_same_multiword_filters(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号名称"))
    article_id = add_article(storage, title="本地阅读指南", content="整理个人知识库")
    add_article(storage, "2", title="本地阅读指南", content="只有其他内容")
    with storage.connect() as db:
        db.execute("DROP TABLE article_fts")
    assert [item["id"] for item in storage.articles(query="本地阅读 个人知识库")] == [article_id]
    assert storage.article_count(query="本地阅读 个人知识库") == 1


def test_source_rename_updates_fts_and_migration_repairs_existing_index(tmp_path):
    path = tmp_path / "data.db"
    storage = Storage(path)
    storage.upsert_source(SourceProfile("MP_WXS_1", "旧的公众号"))
    article_id = add_article(storage)
    storage.upsert_source(SourceProfile("MP_WXS_1", "新的公众号"))
    assert storage.articles(query="新的公众号")[0]["id"] == article_id
    assert storage.articles(query="旧的公众号") == []
    with storage.connect() as db:
        db.execute("UPDATE sources SET name='迁移后名称'")
        db.execute("UPDATE schema_meta SET value='4' WHERE key='schema_version'")
    migrated = Storage(path)
    assert migrated.articles(query="迁移后名称")[0]["id"] == article_id


def test_articles_sort_and_daily_boundaries_use_actual_instant(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.update_settings({"timezone": "Asia/Shanghai"})
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    older = add_article(storage, "older", published_at=datetime.fromisoformat("2026-10-23T23:00:00+08:00"))
    midnight = add_article(storage, "midnight", published_at=datetime.fromisoformat("2026-10-23T16:00:00+00:00"))
    newer = add_article(storage, "newer", published_at=datetime.fromisoformat("2026-10-24T00:30:00+08:00"))
    following_day = add_article(storage, "next", published_at=datetime.fromisoformat("2026-10-24T16:00:00+00:00"))
    assert [item["id"] for item in storage.articles()] == [following_day, newer, midnight, older]
    assert [item["id"] for item in storage.day_articles(date(2026, 10, 24))] == [newer, midnight]


def test_source_due_respects_both_retry_and_cooldown(tmp_path):
    storage = Storage(tmp_path / "data.db")
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    row = {"next_attempt_at": (now - timedelta(hours=1)).isoformat(), "cooldown_until": (now + timedelta(hours=1)).isoformat()}
    assert not storage.source_due(row, now)
    row["next_attempt_at"] = "invalid"
    assert not storage.source_due(row, now)


def test_article_deduplication_does_not_hide_foreign_key_failures(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    article_id = add_article(storage)
    assert add_article(storage) == article_id
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        add_article(storage, "orphan", source_id="missing")


def test_pending_summary_only_returns_captured_unsummarized_articles(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    pending = add_article(storage, "pending")
    fallback = add_article(storage, "fallback")
    done = add_article(storage, "done")
    add_article(storage, "missing-content", content="")
    storage.update_summary(done, {"status": "done", "summary": "摘要"})
    storage.update_summary(fallback, {"status": "fallback"})
    assert {item["id"] for item in storage.pending_summary()} == {pending, fallback}
    assert len(storage.pending_summary(limit=1)) == 1


def test_daily_backup_preserves_first_snapshot_and_removes_temporary_files(tmp_path):
    storage = Storage(tmp_path / "data.db")
    target = next((tmp_path / "backups").glob("*.db"))
    original = target.read_bytes()
    storage.upsert_source(SourceProfile("MP_WXS_1", "后来添加"))
    storage.backup_if_due()
    assert target.read_bytes() == original
    assert not list((tmp_path / "backups").glob("*.tmp"))


def test_legacy_database_columns_are_migrated_before_indexes(tmp_path):
    path = tmp_path / "data.db"
    storage = Storage(path)
    storage.upsert_source(SourceProfile("MP_WXS_1", "旧版公众号"))
    article_id = add_article(storage)
    with storage.connect() as db:
        db.execute("DROP INDEX idx_articles_content_status")
        for column in ("digest", "content_status", "content_error", "raw_summary", "prompt_version", "updated_at"):
            db.execute(f"ALTER TABLE articles DROP COLUMN {column}")
        db.execute("UPDATE schema_meta SET value='3' WHERE key='schema_version'")
    migrated = Storage(path)
    assert migrated.article(article_id)["title"] == "本地阅读"
    assert migrated.article(article_id)["content_status"] == "done"


def test_configured_timezone_uses_bundled_tzdata_without_system_database(tmp_path):
    original_path = zoneinfo.TZPATH
    zoneinfo.reset_tzpath(())
    zoneinfo.ZoneInfo.clear_cache()
    try:
        storage = Storage(tmp_path / "data.db")
        storage.update_settings({"timezone": "America/New_York"})
        start, end = storage._day_bounds(date(2026, 3, 8))
        # The DST transition creates a 23-hour local day, even without system IANA files.
        assert start == "2026-03-08T05:00:00+00:00"
        assert end == "2026-03-09T04:00:00+00:00"
        assert storage.local_now().tzinfo.key == "America/New_York"
    finally:
        zoneinfo.reset_tzpath(original_path)
        zoneinfo.ZoneInfo.clear_cache()
