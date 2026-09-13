from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .models import ArticleRecord, SourceProfile, SyncResult
from .providers import WeReadError


class ReaderService:
    def __init__(self, storage, provider, resolver, fetcher, summarizer, sleep=time.sleep):
        self.storage = storage
        self.provider = provider
        self.resolver = resolver
        self.fetcher = fetcher
        self.summarizer = summarizer
        self.sleep = sleep
        self._sync_lock = threading.Lock()
        self._credential_notified = False

    def resolve_link(self, url: str) -> dict[str, Any]:
        return self.resolver.resolve_source(url)

    def add_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = SourceProfile(
            id=str(payload["id"]),
            name=str(payload["name"]),
            avatar=str(payload.get("avatar") or ""),
            intro=str(payload.get("intro") or ""),
            sample_url=str(payload.get("sample_url") or ""),
            status=str(payload.get("status") or "active"),
        )
        self.storage.upsert_source(source)
        sample = payload.get("sample_article") or {}
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
        if not self._sync_lock.acquire(blocking=False):
            return {"accepted": False, "message": "正在刷新，请稍候"}
        result = SyncResult()
        job_id = self.storage.start_job("sync", source_id)
        try:
            connection = self.storage.connection_state("weread") or {}
            if connection.get("state") == "verification" and connection.get("reconnect_required"):
                message = str(connection.get("message") or "需要在浏览器中重新验证访问")
                payload = result.to_dict()
                self.storage.finish_job(job_id, "completed", payload)
                emit("sync.skipped", {"reason": "verification_required", "message": message})
                return payload
            rows = [
                row for row in self.storage.sources()
                if row["enabled"]
                and row.get("status") != "unsupported"
                and (not source_id or row["id"] == source_id)
                and (force or source_id or self.storage.source_due(row))
            ]
            result.source_count = len(rows)
            emit("sync.started", {"source_count": len(rows), "source_id": source_id})
            stop_for_auth = False
            for index, row in enumerate(rows):
                if stop_for_auth:
                    break
                source = SourceProfile(
                    id=row["id"],
                    name=row["name"],
                    avatar=row["avatar"],
                    intro=row["intro"],
                    sample_url=row["sample_url"],
                    status=row["status"] if row["status"] in {"active", "paused", "limited", "error", "unsupported"} else "active",
                )
                emit("sync.progress", {"current": index + 1, "total": len(rows), "source": source.name, "stage": "list"})
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
                    content_requests = 0
                    for item in batch.articles:
                        article_id, inserted = self.storage.insert_article(item)
                        if inserted:
                            result.inserted += 1
                            if source.id not in result.source_ids:
                                result.source_ids.append(source.id)
                        record = self.storage.article(article_id) if article_id else None
                        if record and record.get("content_status") != "done":
                            if content_requests:
                                self.sleep(2)
                            content_requests += 1
                            self._fill_content(record, item, source, result)
                    for pending in self.storage.pending_content(limit=20, source_id=source.id):
                        if any(item.origin_id == pending["origin_id"] for item in batch.articles):
                            continue
                        if content_requests:
                            self.sleep(2)
                        content_requests += 1
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
                    if first_sync and not batch.articles and not batch.newest_cursor:
                        self.storage.set_source_unsupported(source.id, "暂不支持自动更新")
                        emit("sync.progress", {"current": index + 1, "total": len(rows), "source": source.name, "stage": "unsupported"})
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
                        emit("verification.required", {"source_id": source.id, "message": str(exc)})
                        stop_for_auth = True
                    elif exc.cooldown:
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
                        emit(
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
                            emit("credential.expired", {"message": "登录状态已失效，请重新连接"})
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
                except Exception as exc:
                    delay = self._network_backoff(int(row.get("failure_count") or 0))
                    self.storage.source_sync_failure(source.id, "内容暂时无法更新", delay)
                    result.errors.append(f"{source.name}：内容暂时无法更新")
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            self.storage.update_settings({"last_refresh_at": now})
            payload = result.to_dict()
            self.storage.finish_job(job_id, "failed" if result.errors and not result.inserted else "completed", payload)
            emit("sync.completed", payload)
            if result.inserted:
                emit(
                    "articles.new_batch",
                    {
                        "count": result.inserted,
                        "source_count": len(result.source_ids),
                        "source_ids": result.source_ids,
                    },
                )
            return payload
        except Exception as exc:
            payload = result.to_dict()
            payload["errors"].append(str(exc))
            self.storage.finish_job(job_id, "failed", payload)
            emit("sync.failed", {"message": "刷新没有完成，请稍后再试"})
            return payload
        finally:
            self._sync_lock.release()

    def _fill_content(self, record: dict[str, Any], item: ArticleRecord, source: SourceProfile, result: SyncResult) -> None:
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
        if not content and item.url:
            fetched = self.fetcher.fetch(
                item.url,
                fallback_title=item.title,
                fallback_author=item.author,
                fallback_publish_time=item.published_at,
            )
            content = fetched.content_text
            error_message = fetched.fetch_error or error_message
        self.storage.update_content(record["id"], content, error_message)
        if not content:
            result.content_pending += 1
        if self.storage.settings().get("auto_summary", True):
            summary_input = content or item.digest or item.title
            payload = self.summarizer.summarize(item.title, summary_input, source.name)
            self.storage.update_summary(record["id"], payload)
            result.summarized += 1

    @staticmethod
    def _network_backoff(failure_count: int) -> int:
        return (5, 15, 60)[min(max(failure_count, 0), 2)]
