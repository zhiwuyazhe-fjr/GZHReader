from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

RECONNECT_MESSAGE = "\u5220\u9664\u8d26\u53f7\u540e\u91cd\u65b0\u626b\u7801\u767b\u5f55"
LOCAL_TOKEN_PREFIX = "gzh_local_"
AUTH_FAILURE_LIMIT = 3
SOFT_401_MESSAGE = "\u767b\u5f55\u72b6\u6001\u6682\u65f6\u4e0d\u7a33\u5b9a\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5"


class MPInfoResolver(Protocol):
    def resolve(self, url: str) -> list[dict[str, Any]]:
        ...


@dataclass(slots=True)
class LocalSessionRecord:
    token: str
    account_id: str
    account_name: str
    remote_token: str
    status: str
    last_error: str
    created_at: float
    updated_at: float


class WereadSessionStore:
    def __init__(self, store_path: Path) -> None:
        self.store_path = store_path
        self._lock = threading.Lock()

    def upsert_session(self, *, account_id: str, account_name: str, remote_token: str) -> str:
        now = time.time()
        with self._lock:
            payload = self._load_payload()
            sessions = payload.setdefault("sessions", {})

            existing_token = next(
                (
                    token
                    for token, record in sessions.items()
                    if str(record.get("account_id") or "") == account_id
                ),
                "",
            )
            local_token = existing_token or f"{LOCAL_TOKEN_PREFIX}{secrets.token_urlsafe(24)}"
            sessions[local_token] = {
                "account_id": account_id,
                "account_name": account_name,
                "remote_token": remote_token,
                "status": "active",
                "last_error": "",
                "consecutive_auth_failures": 0,
                "created_at": sessions.get(local_token, {}).get("created_at", now),
                "updated_at": now,
            }
            payload["updated_at"] = now
            self._save_payload(payload)
        return local_token

    def record_success(self, local_token: str) -> None:
        with self._lock:
            payload = self._load_payload()
            record = payload.get("sessions", {}).get(local_token)
            if not isinstance(record, dict):
                return
            record["status"] = "active"
            record["last_error"] = ""
            record["consecutive_auth_failures"] = 0
            record["updated_at"] = time.time()
            payload["updated_at"] = record["updated_at"]
            self._save_payload(payload)

    def record_auth_failure(
        self,
        local_token: str,
        reason: str = RECONNECT_MESSAGE,
    ) -> tuple[int, bool]:
        with self._lock:
            payload = self._load_payload()
            record = payload.get("sessions", {}).get(local_token)
            if not isinstance(record, dict):
                return AUTH_FAILURE_LIMIT, True

            next_failures = int(record.get("consecutive_auth_failures") or 0) + 1
            record["consecutive_auth_failures"] = next_failures
            record["updated_at"] = time.time()

            if next_failures >= AUTH_FAILURE_LIMIT:
                record["status"] = "invalid"
                record["last_error"] = reason
                payload["updated_at"] = record["updated_at"]
                self._save_payload(payload)
                return next_failures, True

            record["status"] = "active"
            record["last_error"] = SOFT_401_MESSAGE
            payload["updated_at"] = record["updated_at"]
            self._save_payload(payload)
            return next_failures, False

    def resolve_remote_token(self, local_token: str) -> str:
        with self._lock:
            payload = self._load_payload()
            record = payload.get("sessions", {}).get(local_token)
            if not isinstance(record, dict):
                raise KeyError(local_token)
            if str(record.get("status") or "") != "active":
                raise PermissionError(str(record.get("last_error") or RECONNECT_MESSAGE))
            remote_token = str(record.get("remote_token") or "").strip()
            if not remote_token:
                raise PermissionError(RECONNECT_MESSAGE)
            return remote_token

    def invalidate(self, local_token: str, reason: str = RECONNECT_MESSAGE) -> None:
        with self._lock:
            payload = self._load_payload()
            record = payload.get("sessions", {}).get(local_token)
            if not isinstance(record, dict):
                return
            record["status"] = "invalid"
            record["last_error"] = reason
            record["updated_at"] = time.time()
            payload["updated_at"] = record["updated_at"]
            self._save_payload(payload)

    def _load_payload(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {"sessions": {}, "updated_at": 0}
        return json.loads(self.store_path.read_text(encoding="utf-8") or "{}") or {
            "sessions": {},
            "updated_at": 0,
        }

    def _save_payload(self, payload: dict[str, Any]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class LocalMPInfoResolver:
    def resolve(self, url: str) -> list[dict[str, Any]]:
        normalized_url = self._normalize_article_url(url)
        metadata = self._resolve_with_browser(normalized_url)
        if not metadata:
            raise RuntimeError("\u73b0\u5728\u8fd8\u65e0\u6cd5\u8bc6\u522b\u8fd9\u6761\u94fe\u63a5")
        return [metadata]

    def _normalize_article_url(self, url: str) -> str:
        value = url.strip()
        if not value:
            raise RuntimeError("\u8bf7\u5148\u7c98\u8d34\u4e00\u6761\u516c\u4f17\u53f7\u6587\u7ae0\u5206\u4eab\u94fe\u63a5")
        parsed = urlparse(value)
        if parsed.netloc != "mp.weixin.qq.com":
            raise RuntimeError("\u8bf7\u786e\u8ba4\u5b83\u6765\u81ea\u516c\u4f17\u53f7\u6587\u7ae0\u9875")
        return value

    def _resolve_with_browser(self, url: str) -> dict[str, Any] | None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(f"Playwright \u4e0d\u53ef\u7528: {exc}") from exc

        last_error = ""
        for launch_kwargs in self._iter_browser_launch_kwargs():
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True, **launch_kwargs)
                    try:
                        page = browser.new_page()
                        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=5_000)
                        except Exception:
                            pass
                        page.wait_for_timeout(1_000)
                        payload = page.evaluate(
                            """
                            () => ({
                              biz: typeof biz !== 'undefined' ? String(biz) : '',
                              nickname: typeof nickname !== 'undefined' ? String(nickname) : '',
                              intro:
                                typeof profile_signature !== 'undefined'
                                  ? String(profile_signature)
                                  : (typeof profile_signature_new !== 'undefined' ? String(profile_signature_new) : ''),
                              headImg:
                                typeof __appmsgCgiData !== 'undefined' && __appmsgCgiData && __appmsgCgiData.hd_head_img
                                  ? String(__appmsgCgiData.hd_head_img)
                                  : (typeof ori_head_img_url !== 'undefined' ? String(ori_head_img_url) : ''),
                              articleCover: typeof msg_cdn_url !== 'undefined' ? String(msg_cdn_url) : '',
                              updateTime: typeof ct !== 'undefined' ? Number(ct) : 0,
                              msgLink: typeof msg_link !== 'undefined' ? String(msg_link) : location.href,
                            })
                            """
                        )
                    finally:
                        browser.close()
            except Exception as exc:  # pragma: no cover - browser/environment dependent
                last_error = str(exc)
                continue

            metadata = self._normalize_metadata(payload, url)
            if metadata:
                return metadata

        if last_error:
            raise RuntimeError(last_error)
        return None

    def _normalize_metadata(self, payload: dict[str, Any], original_url: str) -> dict[str, Any] | None:
        biz = str(payload.get("biz") or "").strip()
        mp_name = str(payload.get("nickname") or "").strip()
        intro = str(payload.get("intro") or "").strip()
        head_img = str(payload.get("headImg") or "").strip() or str(payload.get("articleCover") or "").strip()
        update_time = int(payload.get("updateTime") or 0)
        msg_link = str(payload.get("msgLink") or "").strip() or original_url

        if not biz or not mp_name:
            return None

        decoded_biz = self._decode_biz(biz)
        article_id = self._extract_article_id(msg_link or original_url)
        cover = self._normalize_cover_url(head_img)

        return {
          "id": f"MP_WXS_{decoded_biz}",
          "name": mp_name,
          "cover": cover,
          "intro": intro,
          "updateTime": update_time or int(time.time()),
          "articleId": article_id,
          "articleUrl": msg_link or original_url,
        }

    def _decode_biz(self, biz: str) -> str:
        try:
            decoded = base64.b64decode(biz + "===").decode("utf-8", errors="ignore").strip()
        except Exception:
            decoded = ""
        safe = decoded or biz
        return "".join(ch for ch in safe if ch.isalnum() or ch in {"_", "-"}) or safe

    def _extract_article_id(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if path.startswith("s/"):
            return path.split("/", 1)[1]
        return path.rsplit("/", 1)[-1]

    def _normalize_cover_url(self, value: str) -> str:
        if not value:
            return ""
        result = value.replace("http://", "https://", 1)
        if result.endswith("/132"):
            return f"{result[:-4]}/0"
        return result

    def _iter_browser_launch_kwargs(self) -> list[dict[str, str]]:
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", "")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "")

        candidates = [
            os.path.join(local_app_data, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(program_files, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(program_files_x86, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
        ]

        kwargs: list[dict[str, str]] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate and candidate not in seen and Path(candidate).exists():
                seen.add(candidate)
                kwargs.append({"executable_path": candidate})
        if not kwargs:
            kwargs.extend([{"channel": "msedge"}, {"channel": "chrome"}])
        return kwargs


def create_bridge_app(
    *,
    remote_platform_url: str,
    session_store_path: Path,
    mp_info_resolver: MPInfoResolver | None = None,
) -> FastAPI:
    store = WereadSessionStore(session_store_path)
    remote_base_url = remote_platform_url.rstrip("/")
    resolver = mp_info_resolver or LocalMPInfoResolver()
    app = FastAPI(title="GZHReader Weread Bridge")

    async def proxy_request(
        method: str,
        path: str,
        *,
        local_token: str = "",
        json_body: Any = None,
        query_params: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> JSONResponse:
        if not remote_base_url:
            return JSONResponse(
                {"message": "\u672c\u5730\u4f1a\u8bdd\u6865\u6682\u65f6\u8fd8\u6ca1\u6709\u914d\u7f6e\u8fdc\u7a0b\u517c\u5bb9\u5c42"},
                status_code=503,
            )

        headers: dict[str, str] = {}
        if local_token:
            try:
                remote_token = store.resolve_remote_token(local_token)
            except PermissionError:
                return JSONResponse(
                    {"message": f"WeReadError401: {RECONNECT_MESSAGE}"},
                    status_code=401,
                )
            except KeyError:
                return JSONResponse(
                    {"message": f"WeReadError401: {RECONNECT_MESSAGE}"},
                    status_code=401,
                )
            headers["Authorization"] = f"Bearer {remote_token}"

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.request(
                    method,
                    f"{remote_base_url}{path}",
                    headers=headers,
                    json=json_body,
                    params=query_params,
                )
        except Exception as exc:
            return JSONResponse(
                {"message": f"\u5e73\u53f0\u8fde\u63a5\u5931\u8d25: {exc}"},
                status_code=502,
            )

        try:
            payload = response.json()
        except Exception:
            payload = {"message": response.text or response.reason_phrase or "\u4e0a\u6e38\u5e73\u53f0\u8fd4\u56de\u5f02\u5e38"}

        if response.status_code >= 400:
            message = str(payload.get("message") or payload.get("detail") or response.text or "").strip()
            if response.status_code == 401 and local_token:
                _failures, invalidated = store.record_auth_failure(local_token, RECONNECT_MESSAGE)
                message = f"WeReadError401: {RECONNECT_MESSAGE if invalidated else SOFT_401_MESSAGE}"
            return JSONResponse(
                {"message": message or "\u4e0a\u6e38\u5e73\u53f0\u8bf7\u6c42\u5931\u8d25"},
                status_code=response.status_code,
            )

        if local_token:
            store.record_success(local_token)
        return JSONResponse(payload, status_code=response.status_code)

    def extract_local_token(authorization: str | None) -> str:
        raw_value = (authorization or "").strip()
        if raw_value.lower().startswith("bearer "):
            return raw_value[7:].strip()
        return raw_value

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v2/login/platform")
    async def create_login_session() -> JSONResponse:
        return await proxy_request("GET", "/api/v2/login/platform", timeout_seconds=15.0)

    @app.get("/api/v2/login/platform/{login_id}")
    async def complete_login_session(login_id: str) -> JSONResponse:
        result = await proxy_request(
            "GET",
            f"/api/v2/login/platform/{login_id}",
            timeout_seconds=120.0,
        )
        if result.status_code >= 400:
            return result

        payload = json.loads(result.body.decode("utf-8"))
        account_id = str(payload.get("vid") or "").strip()
        remote_token = str(payload.get("token") or "").strip()
        if account_id and remote_token:
            account_name = str(payload.get("username") or f"\u8d26\u53f7 {account_id}")
            payload["token"] = store.upsert_session(
                account_id=account_id,
                account_name=account_name,
                remote_token=remote_token,
            )
            return JSONResponse(payload, status_code=200)
        return result

    @app.post("/api/v2/platform/wxs2mp")
    async def resolve_mp_link(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        local_token = extract_local_token(authorization)
        if local_token:
            try:
                store.resolve_remote_token(local_token)
            except PermissionError:
                return JSONResponse(
                    {"message": f"WeReadError401: {RECONNECT_MESSAGE}"},
                    status_code=401,
                )
            except KeyError:
                return JSONResponse(
                    {"message": f"WeReadError401: {RECONNECT_MESSAGE}"},
                    status_code=401,
                )

        body = await request.json()
        url = str(body.get("url") or "").strip()

        try:
            payload = resolver.resolve(url)
        except Exception as exc:
            if remote_base_url:
                return await proxy_request(
                    "POST",
                    "/api/v2/platform/wxs2mp",
                    local_token=local_token,
                    json_body=body,
                )
            return JSONResponse(
                {"message": str(exc) or "\u73b0\u5728\u8fd8\u65e0\u6cd5\u8bc6\u522b\u8fd9\u6761\u94fe\u63a5"},
                status_code=502,
            )
        return JSONResponse(payload, status_code=200)

    @app.get("/api/v2/platform/mps/{mp_id}/articles")
    async def refresh_subscription_feed(
        mp_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        return await proxy_request(
            "GET",
            f"/api/v2/platform/mps/{mp_id}/articles",
            local_token=extract_local_token(authorization),
            query_params=dict(request.query_params),
        )

    return app


def run_bridge_server(
    *,
    host: str,
    port: int,
    remote_platform_url: str,
    session_store_path: Path,
) -> None:
    uvicorn.run(
        create_bridge_app(
            remote_platform_url=remote_platform_url,
            session_store_path=session_store_path,
        ),
        host=host,
        port=port,
        log_level="warning",
        log_config=None,
    )
