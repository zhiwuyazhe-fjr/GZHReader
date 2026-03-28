from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

RECONNECT_MESSAGE = "删除账号后，重新扫码添加"
LOCAL_TOKEN_PREFIX = "gzh_local_"


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
                "created_at": sessions.get(local_token, {}).get("created_at", now),
                "updated_at": now,
            }
            payload["updated_at"] = now
            self._save_payload(payload)
        return local_token

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


def create_bridge_app(
    *,
    remote_platform_url: str,
    session_store_path: Path,
) -> FastAPI:
    store = WereadSessionStore(session_store_path)
    remote_base_url = remote_platform_url.rstrip("/")
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
                {"message": f"平台连接失败：{exc}"},
                status_code=502,
            )

        try:
            payload = response.json()
        except Exception:
            payload = {"message": response.text or response.reason_phrase or "上游平台返回异常"}

        if response.status_code >= 400:
            message = str(payload.get("message") or payload.get("detail") or response.text or "").strip()
            if response.status_code == 401 and local_token:
                store.invalidate(local_token, RECONNECT_MESSAGE)
                message = f"WeReadError401: {RECONNECT_MESSAGE}"
            return JSONResponse({"message": message or "上游平台请求失败"}, status_code=response.status_code)

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
            account_name = str(payload.get("username") or f"账号 {account_id}")
            payload["token"] = store.upsert_session(
                account_id=account_id,
                account_name=account_name,
                remote_token=remote_token,
            )
            return JSONResponse(payload, status_code=200)
        return result

    @app.post("/api/v2/platform/wxs2mp")
    async def resolve_mp_link(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        body = await request.json()
        return await proxy_request(
            "POST",
            "/api/v2/platform/wxs2mp",
            local_token=extract_local_token(authorization),
            json_body=body,
        )

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
