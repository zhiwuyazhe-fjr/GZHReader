from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .ai import Summarizer
from .article_fetcher import ArticleContentFetcher
from .briefing import BriefingService
from .browser import WeReadLoginCapture
from .credentials import CredentialVault
from .fetch_config import ArticleFetchConfig, RSSConfig
from .logging_utils import configure_logging
from .paths import AppPaths, get_paths
from .platform_utils import open_local_path, open_web_url
from .providers import ArticleLinkResolver, WeReadProvider
from .scheduler import Scheduler
from .service import ReaderService
from .storage import Storage

logger = logging.getLogger(__name__)


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class CoreApp:
    def __init__(self, emit, paths: AppPaths | None = None, start_scheduler: bool = True):
        self.emit = emit
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        self._auth_thread: threading.Thread | None = None
        self.paths = paths or get_paths()
        configure_logging(self.paths.logs)
        self.storage = Storage(self.paths.db, self.paths.backups)
        self.vault = CredentialVault(self.paths.secrets)
        self.provider = WeReadProvider(self.vault)
        self.resolver = ArticleLinkResolver(
            self.paths.link_browser_profile,
            lambda payload: self.emit("link_resolution.progress", payload),
        )
        self.summarizer = Summarizer(self.vault)
        self.fetcher = ArticleContentFetcher(ArticleFetchConfig(), RSSConfig())
        self.reader = ReaderService(self.storage, self.provider, self.resolver, self.fetcher, self.summarizer)
        self.briefing = BriefingService(self.storage, self.paths.briefings, self.summarizer)
        self.login = WeReadLoginCapture(self.paths.browser_profile, self.vault, self.provider.validate_credentials)
        self.scheduler = Scheduler(
            self.storage,
            lambda: self.reader.sync(self.emit),
            lambda: self.briefing.generate(self.storage.today()),
            self.emit,
        )
        if start_scheduler:
            self.scheduler.start()

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if not isinstance(params, dict):
            raise ValueError("操作参数必须为对象")
        if self._closed and method != "core.shutdown":
            raise ValueError("服务正在关闭")
        if method == "app.bootstrap":
            return self.bootstrap()
        if method == "app.health":
            return {"ok": True, "connection": self._connection_status(), "scheduler": self._scheduler_status()}
        if method == "subscriptions.resolve_link":
            return self.reader.resolve_link(str(params["url"]))
        if method == "subscriptions.add":
            return self.reader.add_source(params["source"])
        if method == "subscriptions.list":
            return self.storage.sources()
        if method == "subscriptions.update":
            self.storage.set_source_enabled(str(params["source_id"]), self._boolean(params, "enabled"))
            return {"ok": True}
        if method == "subscriptions.remove":
            self.storage.remove_source(str(params["source_id"]))
            return {"ok": True}
        if method == "subscriptions.sync":
            return self._background_sync(str(params.get("source_id") or ""), force=True)
        if method == "articles.list":
            return self.storage.articles(
                source_id=str(params.get("source_id") or ""),
                unread_only=self._boolean(params, "unread_only", False),
                query=str(params.get("query") or ""),
                limit=min(self._integer(params, "limit", 200, minimum=1), 500),
                offset=self._integer(params, "offset", 0, minimum=0),
            )
        if method == "articles.get":
            return self._require_article(self._integer(params, "article_id", minimum=1))
        if method == "articles.mark_read":
            self.storage.mark_read(self._integer(params, "article_id", minimum=1), self._boolean(params, "read", True))
            return {"ok": True}
        if method == "articles.retry_summary":
            item = self._require_article(self._integer(params, "article_id", minimum=1))
            payload = self.summarizer.summarize(
                item["title"], item["content"] or item.get("digest", ""), item["source_name"]
            )
            self.storage.update_summary(item["id"], payload)
            self.emit("summary.completed", {"article_id": item["id"]})
            return self._require_article(item["id"])
        if method == "briefings.list":
            return self.storage.briefings()
        if method == "briefings.get":
            return self.storage.briefing(str(params.get("day") or self.storage.today().isoformat()))
        if method == "briefings.generate":
            self.storage.update_settings({"briefing_skip_day": ""})
            result = self.briefing.generate(date.fromisoformat(str(params.get("day") or self.storage.today().isoformat())))
            self.emit("briefing.ready", {"day": result["day"], "file_path": result["file_path"]})
            return result
        if method == "briefings.export":
            briefing = self.storage.briefing(str(params.get("day") or self.storage.today().isoformat()))
            if not briefing:
                raise ValueError("这一天还没有简报")
            return {"file_path": briefing["file_path"]}
        if method == "settings.get":
            return {**self.storage.settings(), "ai": self.summarizer.public_config()}
        if method == "settings.update":
            values = params.get("settings", {})
            if not isinstance(values, dict):
                raise ValueError("设置格式无效")
            return self._update_settings(dict(values))
        if method == "ai.test_connection":
            return self.summarizer.test(params)
        if method == "auth.start":
            self._ensure_auth_allowed()
            return self._background_auth(str(params["source_id"]))
        if method == "auth.cancel":
            self.login.cancel()
            return {"ok": True}
        if method == "auth.reconnect":
            self._ensure_auth_allowed()
            source_id = str(params.get("source_id") or self._first_source_id())
            if not source_id:
                raise ValueError("请先添加一个公众号")
            return self._background_auth(source_id)
        if method == "scheduler.status":
            return self._scheduler_status()
        if method == "scheduler.run_now":
            return self._background_sync(str(params.get("source_id") or ""), force=True)
        if method == "system.open_url":
            open_web_url(str(params["url"]))
            return {"ok": True}
        if method == "system.open_path":
            open_local_path(str(params["path"]))
            return {"ok": True}
        if method == "core.shutdown":
            self.shutdown()
            return {"ok": True, "shutdown": True}
        raise RpcError(-32601, "不支持的操作")

    @staticmethod
    def _integer(params: dict, key: str, default: int | None = None, minimum: int = 0) -> int:
        value = params.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ValueError(f"{key} 必须为不小于 {minimum} 的整数")
        return value

    @staticmethod
    def _boolean(params: dict, key: str, default: bool | None = None) -> bool:
        value = params.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须为布尔值")
        return value

    def _scheduler_status(self) -> dict[str, Any]:
        return {**self.scheduler.status(), "syncing": self.reader.is_syncing}

    def bootstrap(self) -> dict[str, Any]:
        return {
            "dashboard": self.storage.dashboard(),
            "sources": self.storage.sources(),
            "articles": self.storage.articles(limit=200),
            "briefings": self.storage.briefings(),
            "settings": {**self.storage.settings(), "ai": self.summarizer.public_config()},
            "connection": self._connection_status(),
            "scheduler": self._scheduler_status(),
        }

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            auth_thread = self._auth_thread
        self.reader.stop(timeout=0)
        self.login.cancel()
        self.scheduler.stop()
        self.reader.stop()
        if auth_thread and auth_thread.is_alive() and auth_thread is not threading.current_thread():
            auth_thread.join(timeout=3)

    def _connection_status(self) -> dict[str, Any]:
        stored = self.storage.connection_state("weread")
        if stored:
            state = str(stored.get("state") or "")
            raw_until = str(stored.get("cooldown_until") or "")
            if state == "verification":
                return {
                    "state": "verification",
                    "message": str(stored.get("message") or "需要在浏览器中重新验证访问"),
                    "reconnect_required": True,
                    "cooldown_until": "",
                }
            if state == "cooldown":
                try:
                    until = datetime.fromisoformat(raw_until)
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=timezone.utc)
                except ValueError:
                    until = None
                if until and until > datetime.now(timezone.utc):
                    return {
                        "state": "cooldown",
                        "message": str(stored.get("message") or "微信读书暂时限制了验证尝试"),
                        "reconnect_required": True,
                        "cooldown_until": raw_until,
                    }
                return {
                    "state": "disconnected",
                    "message": "可以重新连接微信读书",
                    "reconnect_required": True,
                    "cooldown_until": "",
                }
            if state in {"disconnected", "error"} and stored.get("reconnect_required"):
                return {
                    "state": "disconnected",
                    "message": str(stored.get("message") or "需要重新连接微信读书"),
                    "reconnect_required": True,
                    "cooldown_until": raw_until,
                }
        return self.provider.health().to_dict()

    def _ensure_auth_allowed(self) -> None:
        status = self._connection_status()
        if status.get("state") != "cooldown":
            return
        raw_until = str(status.get("cooldown_until") or "")
        try:
            until = datetime.fromisoformat(raw_until)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            until = until.astimezone()
            label = until.strftime("%m月%d日 %H:%M")
        except ValueError:
            label = "稍后"
        raise ValueError(f"微信暂时限制了验证尝试，请在 {label} 后再试")

    def _update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        old = self.storage.settings()
        ai = values.pop("ai", None)
        if ai is not None:
            ai = self.summarizer._merge_config(ai)
        updated = self.storage.update_settings(values)
        if ai is not None:
            self.summarizer.save(ai)
        refresh_changed = any(
            old.get(key) != updated.get(key) for key in ("refresh_minutes", "refresh_paused", "timezone")
        )
        if refresh_changed:
            self.scheduler.reschedule(reset=True)
        else:
            self.scheduler.reschedule(reset=False)
        briefing_changed = (
            old.get("briefing_time") != updated.get("briefing_time")
            or old.get("briefing_enabled") != updated.get("briefing_enabled")
        )
        due_now = briefing_changed and self.scheduler.briefing_due_now()
        if due_now:
            self.storage.update_settings({"briefing_skip_day": self.storage.today().isoformat()})
        updated = self.storage.settings()
        return {**updated, "ai": self.summarizer.public_config(), "briefing_due_now": due_now}

    def _require_article(self, article_id: int) -> dict[str, Any]:
        item = self.storage.article(article_id)
        if not item:
            raise ValueError("文章不存在或已经删除")
        return item

    def _first_source_id(self) -> str:
        sources = self.storage.sources()
        return str(sources[0]["id"]) if sources else ""

    def _background_sync(self, source_id: str = "", force: bool = False) -> dict[str, Any]:
        with self._lifecycle_lock:
            if self._closed:
                return {"accepted": False, "message": "服务正在关闭"}
            return self.reader.start_sync(self.emit, source_id, force=force)

    def _background_auth(self, source_id: str) -> dict[str, Any]:
        def run() -> None:
            try:
                with self._lifecycle_lock:
                    if self._closed:
                        return
                result = self.login.run(source_id, self.emit)
                with self._lifecycle_lock:
                    if self._closed:
                        return
                    if result.get("ok"):
                        payload = result.get("articles_payload")
                        if isinstance(payload, dict):
                            self.provider.cache_articles_page(source_id, 0, payload)
                        self.storage.set_connection_state("weread", "ready", "微信读书已连接")
                if result.get("ok"):
                    self.reader.sync(self.emit, source_id, force=True)
            except Exception as exc:
                if self._closed:
                    return
                logger.exception("WeRead login failed")
                message = str(exc) or "连接没有完成"
                if "过于频繁" in message or "稍后再试" in message or "24 小时" in message:
                    cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
                    self.storage.set_connection_state("weread", "cooldown", message, True, cooldown_until)
                    self.emit("provider.cooldown", {"message": message, "cooldown_until": cooldown_until})
                else:
                    self.storage.set_connection_state("weread", "disconnected", message, True)
                    self.emit("auth.failed", {"message": message})

        with self._lifecycle_lock:
            if self._closed:
                return {"accepted": False, "message": "服务正在关闭"}
            if self._auth_thread and self._auth_thread.is_alive():
                return {"accepted": False, "message": "正在连接，请稍候"}
            self.login.prepare()
            self._auth_thread = threading.Thread(target=run, name="gzhreader-auth", daemon=True)
            self._auth_thread.start()
            return {"accepted": True}


class RpcServer:
    def __init__(self, app_factory=CoreApp):
        self.lock = threading.Lock()
        self.app = app_factory(self.emit)

    def write(self, payload: dict[str, Any]) -> None:
        with self.lock:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=self._json_default) + "\n")
            sys.stdout.flush()

    @staticmethod
    def _json_default(value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"无法序列化 {type(value).__name__}")

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.write({"jsonrpc": "2.0", "event": event, "payload": payload})

    def run(self) -> None:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            self.emit("core.ready", {"version": __version__})
            for line in sys.stdin:
                if not line.strip():
                    continue
                request: dict[str, Any] | None = None
                request_id = None
                valid_request = False
                try:
                    try:
                        request = json.loads(line.lstrip("\ufeff"))
                    except json.JSONDecodeError as exc:
                        raise RpcError(-32700, "请求 JSON 格式无效") from exc
                    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                        raise RpcError(-32600, "请求格式无效")
                    request_id = request.get("id")
                    if request_id is not None and (not isinstance(request_id, (str, int)) or isinstance(request_id, bool)):
                        request_id = None
                        raise RpcError(-32600, "请求标识格式无效")
                    valid_request = True
                    params = request.get("params", {})
                    if not isinstance(params, dict):
                        raise RpcError(-32602, "操作参数必须为对象")
                    result = self.app.dispatch(request["method"], params)
                    if "id" in request:
                        self.write({"jsonrpc": "2.0", "id": request_id, "result": result})
                    if isinstance(result, dict) and result.get("shutdown"):
                        break
                except Exception as exc:
                    if isinstance(exc, RpcError):
                        code, message = exc.code, str(exc)
                    elif isinstance(exc, (KeyError, TypeError, ValueError)):
                        code, message = -32602, "操作参数无效" if isinstance(exc, KeyError) else str(exc)
                    else:
                        logger.exception("RPC request failed")
                        code, message = -32000, "操作没有完成，请稍后再试"
                    if not valid_request or (isinstance(request, dict) and "id" in request):
                        self.write({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})
        finally:
            self.app.shutdown()


def main() -> None:
    RpcServer().run()
