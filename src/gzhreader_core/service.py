from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .models import ArticleRecord, SourceProfile, SyncResult
from .providers import WeReadError

logger = logging.getLogger(__name__)
MAX_SUMMARIES_PER_SYNC = 20
MAX_CONTENT_REQUESTS_PER_SYNC = 20


class ReaderService:
    def __init__(self, storage, provider, resolver, fetcher, summarizer, sleep=time.sleep):
        self.storage = storage
        self.provider = provider
        self.resolver = resolver
        self.fetcher = fetcher
        self.summarizer = summarizer
        self.sleep = sleep
        self._sync_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._summary_attempts = 0
        self._content_attempts = 0
        self._credential_notified = False

    @property
    def is_syncing(self) -> bool:
        return self._sync_lock.locked()

    def stop(self, timeout: float = 3) -> None:
        self._stop_event.set()
        worker = self._worker
        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=timeout)

    def start_sync(self, emit: Callable[[str, dict], None], source_id: str = "", force: bool = False) -> dict[str, Any]:
        rejection = self._sync_rejection()
        if rejection:
            return rejection
        if not self._sync_lock.acquire(blocking=False):
            return {"accepted": False, "message": "正在刷新，请稍候"}
        try:
            self._worker = threading.Thread(
                target=self._sync_locked, args=(emit, source_id, force), name="gzhreader-manual-sync", daemon=True
            )
            self._worker.start()
        except Exception:
            self._sync_lock.release()
            raise
        return {"accepted": True}

    def resolve_link(self, url: str) -> dict[str, Any]:
        return self.resolver.resolve_source(url)

    def add_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("公众号信息格式无效")
        source_id = payload.get("id")
        name = payload.get("name")
        if not isinstance(source_id, str) or not source_id.strip() or not isinstance(name, str) or not name.strip():
            raise ValueError("请提供公众号标识和名称")
        sample = payload.get("sample_article") or {}
        if not isinstance(sample, dict):
            raise ValueError("示例文章格式无效")
        status = str(payload.get("status") or "active")
        if status not in {"active", "paused", "limited", "error", "unsupported"}:
            raise ValueError("公众号状态无效")
        source = SourceProfile(
            id=source_id.strip(),
            name=name.strip(),
            avatar=str(payload.get("avatar") or ""),
            intro=str(payload.get("intro") or ""),
            sample_url=str(payload.get("sample_url") or ""),
            status=status,
        )
        self.storage.upsert_source(source)
        if sample.get("url"):
            try:
                published_at = datetime.fromisoformat(str(sample.get("published_at") or ""))
            except ValueError:
                published_at = datetime.now(timezone.utc)
            self.storage.insert_article(
                ArticleRecord(
                    source_id=source.id,
                    origin_id=str(sample.get("origin_id") or sample["url"]),
                    title=str(sample.get("title") or "已添加的公众号文章"),
                    url=str(sample["url"]),
                    published_at=published_at,
                    author=str(sample.get("author") or source.name),
                    cover=str(sample.get("cover") or ""),
                )
            )
        return self.storage.source(source.id) or source.to_dict()

    def sync(self, emit: Callable[[str, dict], None], source_id: str = "", force: bool = False) -> dict[str, Any]:
        rejection = self._sync_rejection()
        if rejection:
            return rejection
        if not self._sync_lock.acquire(blocking=False):
            return {"accepted": False, "message": "正在刷新，请稍候"}
        return self._sync_locked(emit, source_id, force)

    def _sync_locked(self, emit: Callable[[str, dict], None], source_id: str, force: bool) -> dict[str, Any]:
        result = SyncResult()
        job_id = None
        terminal_event: tuple[str, dict] | None = None
        self._summary_attempts = 0
        self._content_attempts = 0

        def notify(event: str, payload: dict) -> None:
            try:
                emit(event, payload)
            except Exception:
                logger.exception("Could not publish sync event %s", event)

        try:
            if self._stop_event.is_set():
                return {"accepted": False, "message": "服务正在关闭"}
            job_id = self.storage.start_job("sync", source_id)
            connection = self.storage.connection_state("weread") or {}
            if connection.get("state") == "verification" and connection.get("reconnect_required"):
                message = str(connection.get("message") or "需要在浏览器中重新验证访问")
                payload = result.to_dict()
                self.storage.finish_job(job_id, "completed", payload)
                terminal_event = ("sync.skipped", {"reason": "verification_required", "message": message})
                return payload
            rows = [
                row for row in self.storage.sources()
                if row["enabled"]
                and row.get("status") != "unsupported"
                and (not source_id or row["id"] == source_id)
                and (force or source_id or self.storage.source_due(row))
            ]
            result.source_count = len(rows)
            notify("sync.started", {"source_count": len(rows), "source_id": source_id})
            stop_for_auth = False
            for index, row in enumerate(rows):
                if stop_for_auth or self._stop_event.is_set():
                    break
                source = SourceProfile(
                    id=row["id"],
                    name=row["name"],
                    avatar=row["avatar"],
                    intro=row["intro"],
                    sample_url=row["sample_url"],
                    status=row["status"] if row["status"] in {"active", "paused", "limited", "error", "unsupported"} else "active",
                )
                notify("sync.progress", {"current": index + 1, "total": len(rows), "source": source.name, "stage": "list"})
                try:
                    first_sync = not bool(row.get("initial_sync_done"))
                    cutoff = datetime.now(timezone.utc) - timedelta(days=30) if first_sync else None
                    batch = self.provider.list_articles(
                        source,
                        limit=20 if first_sync else 200,
                        cutoff=cutoff,
                        previous_cursor=int(row.get("sync_cursor") or 0),
                        max_pages=20,
                    )
                    result.fetched += len(batch.articles)
                    summarized_ids: set[int] = set()
                    batch_origin_ids = {item.origin_id for item in batch.articles}
                    for item in batch.articles:
                        if self._stop_event.is_set():
                            break
                        article_id, inserted = self.storage.insert_article(item)
                        if inserted:
                            result.inserted += 1
                            if source.id not in result.source_ids:
                                result.source_ids.append(source.id)
                        record = self.storage.article(article_id) if article_id else None
                        if record and record.get("content_status") != "done":
                            self._fill_content(record, item, source, result)
                            summarized_ids.add(record["id"])
                        elif record:
                            self._summarize(record, source.name, result)
                            summarized_ids.add(record["id"])
                    for pending in self.storage.pending_content(limit=20, source_id=source.id):
                        if self._stop_event.is_set() or self._content_attempts >= MAX_CONTENT_REQUESTS_PER_SYNC:
                            break
                        if pending["origin_id"] in batch_origin_ids:
                            continue
                        item = ArticleRecord(
                            source_id=source.id,
                            origin_id=pending["origin_id"],
                            title=pending["title"],
                            url=pending["url"],
                            published_at=datetime.fromisoformat(pending["published_at"]),
                            author=pending["author"],
                            cover=pending["cover"],
                            digest=pending.get("digest", ""),
                        )
                        self._fill_content(pending, item, source, result)
                        summarized_ids.add(pending["id"])
                    if self._stop_event.is_set():
                        break
                    if self.storage.settings().get("auto_summary", True):
                        for pending in self.storage.pending_summary(limit=MAX_SUMMARIES_PER_SYNC, source_id=source.id):
                            if self._stop_event.is_set() or self._summary_attempts >= MAX_SUMMARIES_PER_SYNC:
                                break
                            if pending["id"] not in summarized_ids:
                                self._summarize(pending, source.name, result)
                    if first_sync and not batch.articles and not batch.newest_cursor:
                        self.storage.set_source_unsupported(source.id, "暂不支持自动更新")
                        notify("sync.progress", {"current": index + 1, "total": len(rows), "source": source.name, "stage": "unsupported"})
                        continue
                    cursor = max(int(row.get("sync_cursor") or 0), int(batch.newest_cursor or 0))
                    self.storage.source_sync_success(source.id, cursor)
                    self.storage.set_connection_state("weread", "ready", "微信读书已连接")
                    self._credential_notified = False
                except WeReadError as exc:
                    self.provider.mark_error(exc)
                    if exc.verification_required:
                        delay = 24 * 60
                        self.storage.set_connection_state("weread", "verification", str(exc), True)
                        notify("verification.required", {"source_id": source.id, "message": str(exc)})
                        stop_for_auth = True
                    elif exc.cooldown:
                        stop_for_auth = True
                        delay = exc.cooldown_minutes
                        cooldown_until = (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat()
                        if exc.reconnect:
                            stop_for_auth = True
                        self.storage.set_connection_state(
                            "weread",
                            "cooldown",
                            str(exc),
                            exc.reconnect,
                            cooldown_until,
                        )
                        notify(
                            "provider.cooldown",
                            {
                                "source_id": source.id,
                                "message": str(exc),
                                "hours": max(1, (delay + 59) // 60),
                                "cooldown_until": cooldown_until,
                            },
                        )
                    elif exc.reconnect:
                        self.provider.vault.clear("weread")
                        self.storage.set_connection_state("weread", "disconnected", "登录状态已失效", True)
                        if not self._credential_notified:
                            notify("credential.expired", {"message": "登录状态已失效，请重新连接"})
                            self._credential_notified = True
                        stop_for_auth = True
                        delay = 60
                    else:
                        delay = self._network_backoff(int(row.get("failure_count") or 0))
                    self.storage.source_sync_failure(
                        source.id,
                        str(exc),
                        delay,
                        cooldown=exc.cooldown or exc.verification_required,
                    )
                    result.errors.append(f"{source.name}：{exc}")
                except Exception:
                    logger.exception("Could not sync source %s", source.id)
                    delay = self._network_backoff(int(row.get("failure_count") or 0))
                    self.storage.source_sync_failure(source.id, "内容暂时无法更新", delay)
                    result.errors.append(f"{source.name}：内容暂时无法更新")
            now = self.storage.local_now().isoformat(timespec="seconds")
            self.storage.update_settings({"last_refresh_at": now})
            payload = result.to_dict()
            if self._stop_event.is_set():
                payload["cancelled"] = True
            status = "cancelled" if payload.get("cancelled") else "failed" if result.errors and not result.inserted else "completed"
            self.storage.finish_job(job_id, status, payload)
            terminal_event = ("sync.completed", payload)
            return payload
        except Exception:
            logger.exception("Sync failed")
            payload = result.to_dict()
            payload["errors"].append("刷新没有完成，请稍后再试")
            if job_id is not None:
                try:
                    self.storage.finish_job(job_id, "failed", payload)
                except Exception:
                    logger.exception("Could not finish sync job %s", job_id)
            terminal_event = ("sync.failed", {"message": "刷新没有完成，请稍后再试"})
            return payload
        finally:
            self._sync_lock.release()
            if terminal_event is not None:
                notify(*terminal_event)
                if result.inserted:
                    notify(
                        "articles.new_batch",
                        {"count": result.inserted, "source_count": len(result.source_ids), "source_ids": result.source_ids},
                    )

    def _fill_content(self, record: dict[str, Any], item: ArticleRecord, source: SourceProfile, result: SyncResult) -> None:
        if self._content_attempts >= MAX_CONTENT_REQUESTS_PER_SYNC:
            result.content_pending += 1
            return
        if self._content_attempts:
            self._pause()
        if self._stop_event.is_set():
            return
        self._content_attempts += 1
        content = ""
        error_message = ""
        try:
            content = self.provider.fetch_content(item.origin_id)
        except WeReadError as exc:
            if exc.reconnect or exc.cooldown:
                raise
            error_message = str(exc)
        except Exception:
            error_message = "正文暂时无法获取"
        if not content and item.url and not self._stop_event.is_set():
            try:
                fetched = self.fetcher.fetch(
                    item.url,
                    fallback_title=item.title,
                    fallback_author=item.author,
                    fallback_publish_time=item.published_at,
                )
                content = fetched.content_text
                error_message = fetched.fetch_error or error_message
            except Exception:
                logger.exception("Could not fetch article %s", record["id"])
                error_message = "正文暂时无法获取"
        if self._stop_event.is_set():
            return
        if content:
            error_message = ""
        self.storage.update_content(record["id"], content, error_message)
        if not content:
            result.content_pending += 1
        self._summarize({**record, "content": content, "digest": item.digest}, source.name, result, force=True)

    def _summarize(self, record: dict[str, Any], source: str, result: SyncResult, force: bool = False) -> None:
        if (
            self._stop_event.is_set()
            or self._summary_attempts >= MAX_SUMMARIES_PER_SYNC
            or not self.storage.settings().get("auto_summary", True)
            or (not force and record.get("summary_status") == "done")
        ):
            return
        self._summary_attempts += 1
        try:
            payload = self.summarizer.summarize(
                record["title"], record.get("content") or record.get("digest") or record["title"], source
            )
            if not self._stop_event.is_set():
                self.storage.update_summary(record["id"], payload)
                result.summarized += 1
        except Exception:
            logger.exception("Could not summarize article %s", record["id"])
            message = f"{source}：文章摘要暂时无法生成"
            if message not in result.errors:
                result.errors.append(message)

    def _pause(self) -> None:
        if self.sleep is time.sleep:
            self._stop_event.wait(2)
        else:
            self.sleep(2)

    def _sync_rejection(self) -> dict[str, Any] | None:
        if self._stop_event.is_set():
            return {"accepted": False, "message": "服务正在关闭"}
        state = self.storage.connection_state("weread")
        if state and state.get("state") == "cooldown":
            try:
                until = datetime.fromisoformat(str(state.get("cooldown_until") or ""))
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                if until > datetime.now(timezone.utc):
                    return {"accepted": False, "message": "微信读书暂时限制了更新，请稍后再试"}
            except ValueError:
                pass
        return None

    @staticmethod
    def _network_backoff(failure_count: int) -> int:
        return (5, 15, 60)[min(max(failure_count, 0), 2)]
