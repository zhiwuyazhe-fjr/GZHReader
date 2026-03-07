from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .config import FeedConfig
from .types import ArticleDraft, ArticleView, StoredArticle


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._migrate_legacy_schema(conn)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feeds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1,
                    feed_order INTEGER NOT NULL DEFAULT 0,
                    author TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_key TEXT PRIMARY KEY,
                    target_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    collected INTEGER NOT NULL DEFAULT 0,
                    inserted INTEGER NOT NULL DEFAULT 0,
                    summarized INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    briefing_path TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    feed_name TEXT NOT NULL,
                    feed_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL,
                    publish_time TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    full_content TEXT NOT NULL DEFAULT '',
                    raw_html TEXT NOT NULL DEFAULT '',
                    content_source TEXT NOT NULL DEFAULT 'title_only',
                    capture_status TEXT NOT NULL DEFAULT 'rss_empty',
                    summary TEXT NOT NULL DEFAULT '',
                    summary_status TEXT NOT NULL DEFAULT 'pending',
                    summary_error TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL UNIQUE,
                    run_key TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS briefings (
                    target_date TEXT PRIMARY KEY,
                    markdown TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_feeds_url_unique
                ON feeds(url);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_url_unique
                ON articles(url)
                WHERE url <> '';

                CREATE INDEX IF NOT EXISTS idx_articles_publish_time
                ON articles(publish_time);
                """
            )

    def upsert_feeds(self, feeds: list[FeedConfig]) -> None:
        with self._connect() as conn:
            for feed in feeds:
                conn.execute(
                    """
                    INSERT INTO feeds(name, url, active, feed_order, author, tags_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        name = excluded.name,
                        active = excluded.active,
                        feed_order = excluded.feed_order,
                        author = excluded.author,
                        tags_json = excluded.tags_json
                    """,
                    (feed.name, feed.url, int(feed.active), feed.order, feed.author or '', '[]'),
                )

    def list_feeds(self, active_only: bool = True, name: str | None = None) -> list[FeedConfig]:
        sql = 'SELECT name, url, active, feed_order, author FROM feeds'
        clauses: list[str] = []
        params: list[object] = []
        if active_only:
            clauses.append('active = 1')
        if name:
            clauses.append('name = ?')
            params.append(name)
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        sql += ' ORDER BY feed_order ASC, name ASC'
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            FeedConfig(
                name=row[0],
                url=row[1],
                active=bool(row[2]),
                order=int(row[3]),
                author=row[4] or None,
            )
            for row in rows
        ]

    def start_run(self, run_key: str, target_date: date) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs(run_key, target_date, status, collected, inserted, summarized, error_count, briefing_path, notes) VALUES (?, ?, 'running', 0, 0, 0, 0, '', '')",
                (run_key, target_date.isoformat()),
            )

    def finish_run(
        self,
        run_key: str,
        *,
        status: str,
        collected: int,
        inserted: int,
        summarized: int,
        error_count: int,
        briefing_path: str,
        notes: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?, collected = ?, inserted = ?, summarized = ?, error_count = ?,
                    briefing_path = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE run_key = ?
                """,
                (status, collected, inserted, summarized, error_count, briefing_path, notes, run_key),
            )

    def insert_article_if_new(self, draft: ArticleDraft, run_key: str) -> bool:
        with self._connect() as conn:
            if draft.url:
                existing = conn.execute('SELECT 1 FROM articles WHERE url = ?', (draft.url,)).fetchone()
                if existing:
                    return False
            existing = conn.execute('SELECT 1 FROM articles WHERE fingerprint = ?', (draft.fingerprint,)).fetchone()
            if existing:
                return False
            conn.execute(
                """
                INSERT INTO articles(
                    feed_name, feed_url, title, author, publish_time, url, full_content, raw_html,
                    content_source, capture_status, summary, summary_status, summary_error, fingerprint, run_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.feed_name,
                    draft.feed_url,
                    draft.title,
                    draft.author,
                    draft.publish_time.isoformat(),
                    draft.url,
                    draft.full_content,
                    draft.raw_html,
                    draft.content_source,
                    draft.capture_status,
                    draft.summary,
                    draft.summary_status,
                    draft.summary_error,
                    draft.fingerprint,
                    run_key,
                ),
            )
            return True

    def get_unsummarized_for_date(self, target_date: date) -> list[StoredArticle]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, feed_name, feed_url, title, author, publish_time, url, full_content, raw_html,
                       content_source, capture_status, summary, summary_status, summary_error
                FROM articles
                WHERE substr(publish_time, 1, 10) = ? AND summary_status = 'pending'
                ORDER BY publish_time ASC, id ASC
                """,
                (target_date.isoformat(),),
            ).fetchall()
        return [self._row_to_stored_article(row) for row in rows]

    def mark_article_summarized(self, article_id: int, summary: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE articles SET summary = ?, summary_status = 'done', summary_error = '' WHERE id = ?",
                (summary, article_id),
            )

    def mark_article_summary_failed(self, article_id: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE articles SET summary_status = 'failed', summary_error = ? WHERE id = ?",
                (error, article_id),
            )

    def get_article_views_for_date(self, target_date: date) -> list[ArticleView]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, feed_name, feed_url, title, author, publish_time, url, full_content,
                       content_source, capture_status, summary, summary_status, summary_error
                FROM articles
                WHERE substr(publish_time, 1, 10) = ?
                ORDER BY feed_name ASC, publish_time ASC, id ASC
                """,
                (target_date.isoformat(),),
            ).fetchall()
        return [
            ArticleView(
                id=row[0],
                feed_name=row[1],
                feed_url=row[2],
                title=row[3],
                author=row[4],
                publish_time=datetime.fromisoformat(row[5]),
                url=row[6],
                full_content=row[7],
                content_source=row[8],
                capture_status=row[9],
                summary=row[10],
                summary_status=row[11],
                summary_error=row[12],
            )
            for row in rows
        ]

    def save_briefing(self, target_date: date, markdown: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO briefings(target_date, markdown)
                VALUES (?, ?)
                ON CONFLICT(target_date) DO UPDATE SET
                    markdown = excluded.markdown,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (target_date.isoformat(), markdown),
            )

    def _migrate_legacy_schema(self, conn: sqlite3.Connection) -> None:
        required_columns = {
            'feeds': {'name', 'url', 'active', 'feed_order', 'author', 'tags_json'},
            'runs': {'run_key', 'target_date', 'status', 'collected', 'inserted', 'summarized', 'error_count', 'briefing_path', 'notes', 'created_at', 'updated_at'},
            'articles': {'feed_name', 'feed_url', 'title', 'author', 'publish_time', 'url', 'full_content', 'raw_html', 'content_source', 'capture_status', 'summary', 'summary_status', 'summary_error', 'fingerprint', 'run_key'},
            'briefings': {'target_date', 'markdown', 'created_at', 'updated_at'},
        }
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        for table, expected in required_columns.items():
            existing = self._table_columns(conn, table)
            if not existing:
                continue
            if expected.issubset(existing):
                continue
            backup_name = f'{table}_legacy_{timestamp}'
            conn.execute(f'ALTER TABLE "{table}" RENAME TO "{backup_name}"')

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        return {str(row[1]) for row in rows}

    def _row_to_stored_article(self, row: sqlite3.Row | tuple) -> StoredArticle:
        return StoredArticle(
            id=row[0],
            feed_name=row[1],
            feed_url=row[2],
            title=row[3],
            author=row[4],
            publish_time=datetime.fromisoformat(row[5]),
            url=row[6],
            full_content=row[7],
            raw_html=row[8],
            content_source=row[9],
            capture_status=row[10],
            summary=row[11],
            summary_status=row[12],
            summary_error=row[13],
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def build_fingerprint(feed_name: str, published_date: date, title: str, url: str, content: str) -> str:
    normalized = "\n".join(
        [
            feed_name.strip(),
            published_date.isoformat(),
            title.strip(),
            url.strip(),
            " ".join(content.split())[:2000],
        ]
    )
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
