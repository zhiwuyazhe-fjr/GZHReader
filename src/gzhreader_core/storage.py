from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ArticleRecord, SourceProfile

SCHEMA_VERSION = 5
REFRESH_OPTIONS = {0, 15, 30, 60, 120, 240}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


DEFAULT_SETTINGS: dict[str, Any] = {
    "refresh_minutes": 60,
    "refresh_paused": False,
    "briefing_enabled": True,
    "briefing_time": "21:30",
    "auto_summary": True,
    "autostart": True,
    "theme": "light",
    "timezone": "Asia/Shanghai",
    "last_refresh_at": "",
    "next_refresh_at": "",
    "briefing_skip_day": "",
}


class Storage:
    def __init__(self, path: Path, backup_dir: Path | None = None):
        self.path = path
        self.backup_dir = backup_dir or path.parent / "backups"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()
        self.backup_if_due()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def init_db(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources(
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    avatar TEXT NOT NULL DEFAULT '',
                    intro TEXT NOT NULL DEFAULT '',
                    sample_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sync_cursor INTEGER NOT NULL DEFAULT 0,
                    initial_sync_done INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL DEFAULT '',
                    cooldown_until TEXT NOT NULL DEFAULT '',
                    last_attempt_at TEXT NOT NULL DEFAULT '',
                    last_success_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    unsupported_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS articles(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    origin_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    cover TEXT NOT NULL DEFAULT '',
                    digest TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    content_status TEXT NOT NULL DEFAULT 'pending',
                    content_error TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    takeaway TEXT NOT NULL DEFAULT '',
                    key_points TEXT NOT NULL DEFAULT '[]',
                    tags TEXT NOT NULL DEFAULT '[]',
                    raw_summary TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    summary_status TEXT NOT NULL DEFAULT 'pending',
                    is_read INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_id, origin_id),
                    UNIQUE(url)
                );
                CREATE TABLE IF NOT EXISTS briefings(
                    day TEXT PRIMARY KEY,
                    overview TEXT NOT NULL DEFAULT '',
                    markdown TEXT NOT NULL,
                    article_count INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_jobs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    details TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS connection_state(
                    provider TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    reconnect_required INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )
            # These additions only upgrade databases created by pre-release v3 builds.
            self._ensure_columns(db)
            previous_version = db.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            db.executescript(
                """
                DROP INDEX IF EXISTS idx_articles_published;
                DROP INDEX IF EXISTS idx_articles_source_published;
                CREATE INDEX IF NOT EXISTS idx_articles_content_status ON articles(content_status);
                CREATE INDEX IF NOT EXISTS idx_articles_instant ON articles(julianday(published_at) DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_articles_source_instant ON articles(source_id, julianday(published_at) DESC, id DESC);
                """
            )
            db.execute(
                "INSERT INTO schema_meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            for key, value in DEFAULT_SETTINGS.items():
                db.execute(
                    "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
            self._init_fts(db, rebuild=not previous_version or previous_version[0] != str(SCHEMA_VERSION))

    def _ensure_columns(self, db: sqlite3.Connection) -> None:
        additions = {
            "sources": {
                "sync_cursor": "INTEGER NOT NULL DEFAULT 0",
                "initial_sync_done": "INTEGER NOT NULL DEFAULT 0",
                "failure_count": "INTEGER NOT NULL DEFAULT 0",
                "next_attempt_at": "TEXT NOT NULL DEFAULT ''",
                "cooldown_until": "TEXT NOT NULL DEFAULT ''",
                "last_attempt_at": "TEXT NOT NULL DEFAULT ''",
                "unsupported_reason": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
            "connection_state": {
                "cooldown_until": "TEXT NOT NULL DEFAULT ''",
            },
            "articles": {
                "digest": "TEXT NOT NULL DEFAULT ''",
                "content_status": "TEXT NOT NULL DEFAULT 'pending'",
                "content_error": "TEXT NOT NULL DEFAULT ''",
                "raw_summary": "TEXT NOT NULL DEFAULT ''",
                "prompt_version": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, columns in additions.items():
            existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {declaration}')
                    if table == "articles" and name == "content_status":
                        db.execute("UPDATE articles SET content_status='done' WHERE length(trim(content))>0")

    def _init_fts(self, db: sqlite3.Connection, rebuild: bool = False) -> None:
        try:
            existing = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='article_fts'"
            ).fetchone()
            if existing and (
                "content=''" in str(existing[0]).replace(" ", "")
                or "trigram" not in str(existing[0]).lower()
            ):
                db.execute("DROP TABLE article_fts")
            db.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5(
                    title, content, summary, source_name, tokenize='trigram'
                );
                CREATE TRIGGER IF NOT EXISTS articles_fts_delete AFTER DELETE ON articles BEGIN
                    DELETE FROM article_fts WHERE rowid=old.id;
                END;
                """
            )
            # FTS is maintained explicitly so source_name can be indexed.
            count = db.execute("SELECT COUNT(*) FROM article_fts").fetchone()[0]
            if rebuild or count == 0:
                db.execute("DELETE FROM article_fts")
                db.execute(
                    "INSERT INTO article_fts(rowid,title,content,summary,source_name) "
                    "SELECT a.id,a.title,a.content,a.summary,s.name FROM articles a JOIN sources s ON s.id=a.source_id"
                )
        except sqlite3.OperationalError:
            # Some custom SQLite builds omit FTS5; LIKE search remains available.
            pass

    def backup_if_due(self) -> None:
        if not self.path.exists():
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        target = self.backup_dir / f"gzhreader-{today}.db"
        if target.exists():
            return
        with tempfile.NamedTemporaryFile(prefix=f".gzhreader-{today}-", suffix=".tmp", dir=self.backup_dir, delete=False) as handle:
            temporary = Path(handle.name)
        try:
            with self.connect() as source, sqlite3.connect(temporary, factory=ClosingConnection) as destination:
                source.backup(destination)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        backups = sorted(self.backup_dir.glob("gzhreader-*.db"), reverse=True)
        for old in backups[7:]:
            old.unlink(missing_ok=True)

    def settings(self) -> dict[str, Any]:
        with self.connect() as db:
            values = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key,value FROM settings")}
        return {**DEFAULT_SETTINGS, **values}

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in values.items():
            if key not in DEFAULT_SETTINGS:
                continue
            if key == "refresh_minutes":
                value = int(value)
                if value not in REFRESH_OPTIONS:
                    raise ValueError("请选择可用的刷新频率")
            elif key == "briefing_time":
                value = str(value)
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                    raise ValueError("请选择正确的简报时间")
            elif key == "theme" and value not in {"light", "dark", "system"}:
                value = "light"
            clean[key] = value
        with self.connect() as db:
            for key, value in clean.items():
                db.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
        return self.settings()

    def upsert_source(self, source: SourceProfile) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO sources(id,name,avatar,intro,sample_url,status,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, avatar=excluded.avatar, intro=excluded.intro,
                    sample_url=excluded.sample_url, status=excluded.status,
                    enabled=1, updated_at=excluded.updated_at
                """,
                (source.id, source.name, source.avatar, source.intro, source.sample_url, source.status, now),
            )
            try:
                db.execute(
                    "UPDATE article_fts SET source_name=? "
                    "WHERE rowid IN (SELECT id FROM articles WHERE source_id=?) AND source_name<>?",
                    (source.name, source.id, source.name),
                )
            except sqlite3.OperationalError:
                pass

    def source(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def sources(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY created_at DESC")]

    def set_source_enabled(self, source_id: str, enabled: bool) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE sources SET enabled=?,status=?,updated_at=? WHERE id=?",
                (int(enabled), "active" if enabled else "paused", datetime.now(timezone.utc).isoformat(), source_id),
            )

    def set_source_unsupported(self, source_id: str, reason: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE sources SET status='unsupported',unsupported_reason=?,last_error=?,updated_at=? WHERE id=?",
                (reason, reason, datetime.now(timezone.utc).isoformat(), source_id),
            )

    def remove_source(self, source_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sources WHERE id=?", (source_id,))

    def source_sync_success(self, source_id: str, cursor: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                """
                UPDATE sources SET sync_cursor=?,initial_sync_done=1,failure_count=0,
                next_attempt_at='',cooldown_until='',last_attempt_at=?,last_success_at=?,
                last_error='',status=CASE WHEN enabled=1 THEN 'active' ELSE status END,updated_at=?
                WHERE id=?
                """,
                (cursor, now, now, now, source_id),
            )

    def source_sync_failure(self, source_id: str, message: str, delay_minutes: int, cooldown: bool = False) -> None:
        now = datetime.now(timezone.utc)
        retry_at = now + timedelta(minutes=delay_minutes)
        with self.connect() as db:
            db.execute(
                """
                UPDATE sources SET failure_count=failure_count+1,next_attempt_at=?,cooldown_until=?,
                last_attempt_at=?,last_error=?,status=?,updated_at=? WHERE id=?
                """,
                (
                    retry_at.isoformat(),
                    retry_at.isoformat() if cooldown else "",
                    now.isoformat(),
                    message,
                    "limited" if cooldown else "error",
                    now.isoformat(),
                    source_id,
                ),
            )

    def source_due(self, row: dict[str, Any], now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        for key in ("next_attempt_at", "cooldown_until"):
            value = row.get(key)
            if not value:
                continue
            try:
                target = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                continue
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            if current < target:
                return False
        return True

    def insert_article(self, article: ArticleRecord, content_status: str | None = None) -> tuple[int, bool]:
        status = content_status or ("done" if article.content.strip() else "pending")
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO articles(
                    source_id,origin_id,title,author,url,published_at,cover,digest,content,content_status
                ) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING
                """,
                (
                    article.source_id,
                    article.origin_id,
                    article.title,
                    article.author,
                    article.url,
                    article.published_at.isoformat(),
                    article.cover,
                    article.digest,
                    article.content,
                    status,
                ),
            )
            if cursor.rowcount:
                article_id = int(cursor.lastrowid)
                self._fts_upsert(db, article_id)
                return article_id, True
            row = db.execute(
                "SELECT id FROM articles WHERE (source_id=? AND origin_id=?) OR url=? "
                "ORDER BY CASE WHEN source_id=? AND origin_id=? THEN 0 ELSE 1 END, id LIMIT 1",
                (article.source_id, article.origin_id, article.url, article.source_id, article.origin_id),
            ).fetchone()
            return (int(row[0]) if row else 0), False

    def update_content(self, article_id: int, content: str, error: str = "") -> None:
        status = "done" if content.strip() else "pending"
        with self.connect() as db:
            db.execute(
                "UPDATE articles SET content=?,content_status=?,content_error=?,updated_at=? WHERE id=?",
                (content, status, error, datetime.now(timezone.utc).isoformat(), article_id),
            )
            self._fts_upsert(db, article_id)

    def pending_content(self, limit: int = 20, source_id: str = "") -> list[dict[str, Any]]:
        where = "a.content_status!='done'"
        args: list[Any] = []
        if source_id:
            where += " AND a.source_id=?"
            args.append(source_id)
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT a.*,s.name source_name,s.avatar source_avatar
                FROM articles a JOIN sources s ON s.id=a.source_id
                WHERE {where} ORDER BY julianday(a.published_at) DESC, a.id DESC LIMIT ?
                """,
                (*args, max(0, int(limit))),
            ).fetchall()
        return [self._article_dict(row) for row in rows]

    def pending_summary(self, limit: int = 20, source_id: str = "") -> list[dict[str, Any]]:
        clauses = ["a.content_status='done'", "a.summary_status!='done'"]
        args: list[Any] = []
        if source_id:
            clauses.append("a.source_id=?")
            args.append(source_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT a.*,s.name source_name,s.avatar source_avatar "
                "FROM articles a JOIN sources s ON s.id=a.source_id "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY julianday(a.published_at) DESC, a.id DESC LIMIT ?",
                (*args, max(0, int(limit))),
            ).fetchall()
        return [self._article_dict(row) for row in rows]

    def articles(
        self, source_id: str = "", unread_only: bool = False, query: str = "",
        limit: int = 200, offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = self._query_articles(source_id, unread_only, query, limit, offset)
        return [self._article_dict(row) for row in rows]

    def article_count(self, source_id: str = "", unread_only: bool = False, query: str = "") -> int:
        return int(self._query_articles(source_id, unread_only, query, count=True)[0][0])

    def _query_articles(
        self, source_id: str, unread_only: bool, query: str, limit: int = 200,
        offset: int = 0, *, count: bool = False,
    ) -> list[sqlite3.Row]:
        tokens = query.split()
        # The trigram tokenizer cannot match fewer than three characters.
        use_fts = bool(tokens) and all(len(token) >= 3 for token in tokens)
        while True:
            clauses: list[str] = []
            args: list[Any] = []
            tables = "articles a JOIN sources s ON s.id=a.source_id"
            if source_id:
                clauses.append("a.source_id=?")
                args.append(source_id)
            if unread_only:
                clauses.append("a.is_read=0")
            if use_fts:
                tables += " JOIN article_fts f ON f.rowid=a.id"
                clauses.append("article_fts MATCH ?")
                args.append(" AND ".join('"' + token.replace('"', '""') + '"' for token in tokens))
            else:
                for token in tokens:
                    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    clauses.append(
                        "(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in
                        ("a.title", "a.content", "a.summary", "s.name")) + ")"
                    )
                    args.extend([f"%{escaped}%"] * 4)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            select = "COUNT(*)" if count else "a.*,s.name source_name,s.avatar source_avatar"
            suffix = "" if count else " ORDER BY julianday(a.published_at) DESC, a.id DESC LIMIT ? OFFSET ?"
            if not count:
                args.extend((max(0, int(limit)), max(0, int(offset))))
            try:
                with self.connect() as db:
                    return db.execute(f"SELECT {select} FROM {tables}{where}{suffix}", args).fetchall()
            except sqlite3.OperationalError:
                if not use_fts:
                    raise
                # SQLite builds without FTS5 still support the same search filters.
                use_fts = False

    def article_by_url(self, url: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT a.*,s.name source_name,s.avatar source_avatar FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.url=?",
                (url,),
            ).fetchone()
        return self._article_dict(row) if row else None

    def article(self, article_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT a.*,s.name source_name,s.avatar source_avatar FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.id=?",
                (article_id,),
            ).fetchone()
        return self._article_dict(row) if row else None

    @staticmethod
    def _article_dict(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for key in ("key_points", "tags"):
            try:
                value[key] = json.loads(value.get(key) or "[]")
            except (TypeError, json.JSONDecodeError):
                value[key] = []
        return value

    def mark_read(self, article_id: int, read: bool = True) -> None:
        with self.connect() as db:
            db.execute("UPDATE articles SET is_read=?,updated_at=? WHERE id=?", (int(read), datetime.now(timezone.utc).isoformat(), article_id))

    def update_summary(self, article_id: int, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE articles SET summary=?,takeaway=?,key_points=?,tags=?,raw_summary=?,
                prompt_version=?,summary_status=?,updated_at=? WHERE id=?
                """,
                (
                    payload.get("summary", ""),
                    payload.get("takeaway", ""),
                    json.dumps(payload.get("key_points", []), ensure_ascii=False),
                    json.dumps(payload.get("tags", []), ensure_ascii=False),
                    payload.get("raw_text", ""),
                    payload.get("prompt_version", ""),
                    payload.get("status", "done"),
                    datetime.now(timezone.utc).isoformat(),
                    article_id,
                ),
            )
            self._fts_upsert(db, article_id)

    def _fts_upsert(self, db: sqlite3.Connection, article_id: int) -> None:
        try:
            db.execute("DELETE FROM article_fts WHERE rowid=?", (article_id,))
            db.execute(
                """
                INSERT INTO article_fts(rowid,title,content,summary,source_name)
                SELECT a.id,a.title,a.content,a.summary,s.name
                FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.id=?
                """,
                (article_id,),
            )
        except sqlite3.OperationalError:
            pass

    def day_articles(self, day: date) -> list[dict[str, Any]]:
        start, end = self._day_bounds(day)
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT a.*,s.name source_name FROM articles a JOIN sources s ON s.id=a.source_id
                WHERE julianday(a.published_at)>=julianday(?) AND julianday(a.published_at)<julianday(?)
                ORDER BY julianday(a.published_at) DESC, a.id DESC
                """,
                (start, end),
            ).fetchall()
        return [self._article_dict(row) for row in rows]

    def save_briefing(self, day: date, overview: str, markdown: str, file_path: str, count: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO briefings(day,overview,markdown,article_count,file_path,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET
                overview=excluded.overview,markdown=excluded.markdown,
                article_count=excluded.article_count,file_path=excluded.file_path,
                updated_at=excluded.updated_at
                """,
                (day.isoformat(), overview, markdown, count, file_path, now, now),
            )

    def briefings(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM briefings ORDER BY day DESC")]

    def briefing(self, day: date | str) -> dict[str, Any] | None:
        day_value = day.isoformat() if isinstance(day, date) else day
        with self.connect() as db:
            row = db.execute("SELECT * FROM briefings WHERE day=?", (day_value,)).fetchone()
        return dict(row) if row else None

    def dashboard(self) -> dict[str, Any]:
        today = self.today()
        start, end = self._day_bounds(today)
        with self.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM articles WHERE julianday(published_at)>=julianday(?) "
                "AND julianday(published_at)<julianday(?)", (start, end),
            ).fetchone()[0]
            unread = db.execute("SELECT COUNT(*) FROM articles WHERE is_read=0").fetchone()[0]
            briefing = db.execute("SELECT * FROM briefings WHERE day=?", (today.isoformat(),)).fetchone()
        return {"today_count": count, "unread_count": unread, "today_briefing": dict(briefing) if briefing else None}

    def _reading_timezone(self):
        try:
            return ZoneInfo(str(self.settings().get("timezone") or "Asia/Shanghai"))
        except (ZoneInfoNotFoundError, ValueError):
            return datetime.now().astimezone().tzinfo or timezone.utc

    def local_now(self) -> datetime:
        return datetime.now(self._reading_timezone())

    def today(self) -> date:
        return self.local_now().date()

    def _day_bounds(self, day: date) -> tuple[str, str]:
        zone = self._reading_timezone()
        start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone)
        return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()

    def start_job(self, kind: str, source_id: str = "") -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO sync_jobs(source_id,kind,status,started_at) VALUES(?,?,?,?)",
                (source_id, kind, "running", datetime.now(timezone.utc).isoformat()),
            )
            return int(cursor.lastrowid)

    def finish_job(self, job_id: int, status: str, details: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE sync_jobs SET status=?,finished_at=?,details=? WHERE id=?",
                (status, datetime.now(timezone.utc).isoformat(), json.dumps(details, ensure_ascii=False), job_id),
            )

    def set_connection_state(
        self,
        provider: str,
        state: str,
        message: str,
        reconnect_required: bool = False,
        cooldown_until: str = "",
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO connection_state(provider,state,message,reconnect_required,cooldown_until,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET state=excluded.state,
                message=excluded.message,reconnect_required=excluded.reconnect_required,
                cooldown_until=excluded.cooldown_until,updated_at=excluded.updated_at
                """,
                (
                    provider,
                    state,
                    message,
                    int(reconnect_required),
                    cooldown_until,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def connection_state(self, provider: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM connection_state WHERE provider=?", (provider,)).fetchone()
        return dict(row) if row else None
