from datetime import datetime
import sqlite3

from gzhreader.config import FeedConfig
from gzhreader.storage import Storage
from gzhreader.types import ArticleDraft


def test_storage_deduplicates_articles(tmp_path) -> None:
    storage = Storage(str(tmp_path / "app.db"))
    storage.init_db()
    storage.upsert_feeds([FeedConfig(name="???", url="http://localhost/feed.atom", active=True, order=1)])

    draft = ArticleDraft(
        feed_name="???",
        feed_url="http://localhost/feed.atom",
        title="??1",
        author="??A",
        publish_time=datetime(2026, 3, 7, 8, 0),
        url="https://example.com/a1",
        full_content="??",
        raw_html="<p>??</p>",
        content_source="rss_content",
        capture_status="rss_fulltext",
        fingerprint="fingerprint-1",
    )

    assert storage.insert_article_if_new(draft, run_key="run1") is True
    assert storage.insert_article_if_new(draft, run_key="run1") is False
    assert len(storage.get_article_views_for_date(datetime(2026, 3, 7).date())) == 1


def test_storage_migrates_legacy_schema(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_key VARCHAR(32) NOT NULL,
            target_date DATE NOT NULL,
            status VARCHAR(32) NOT NULL,
            collected_count INTEGER NOT NULL,
            inserted_count INTEGER NOT NULL,
            summarized_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            briefing_path VARCHAR(500) NOT NULL,
            notes TEXT NOT NULL,
            started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME NULL
        );
        """
    )
    conn.close()

    storage = Storage(str(db_path))
    storage.init_db()
    storage.start_run("run123", datetime(2026, 3, 7).date())

    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    assert "runs" in tables
    assert any(name.startswith("runs_legacy_") for name in tables)
