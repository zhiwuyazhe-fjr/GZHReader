from pathlib import Path
import json

import httpx
from fastapi.testclient import TestClient

from gzhreader.weread_bridge import (
    LOCAL_TOKEN_PREFIX,
    RECONNECT_MESSAGE,
    SOFT_401_MESSAGE,
    create_bridge_app,
)


def test_bridge_wraps_remote_login_token_as_local_session(monkeypatch, tmp_path: Path) -> None:
    async def fake_request(self, method, url, headers=None, json=None, params=None, timeout=None):  # noqa: ANN001
        assert method == "GET"
        assert url.endswith("/api/v2/login/platform/demo")
        return httpx.Response(
            200,
            json={
                "vid": 42,
                "token": "remote-platform-token",
                "username": "demo-user",
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    app = create_bridge_app(
        remote_platform_url="https://example.com",
        session_store_path=tmp_path / "sessions.json",
    )
    client = TestClient(app)

    response = client.get("/api/v2/login/platform/demo")

    assert response.status_code == 200
    payload = response.json()
    assert str(payload["token"]).startswith(LOCAL_TOKEN_PREFIX)
    stored = (tmp_path / "sessions.json").read_text(encoding="utf-8")
    assert "remote-platform-token" in stored


def test_bridge_rejects_unknown_local_session(monkeypatch, tmp_path: Path) -> None:
    async def fake_request(self, method, url, headers=None, json=None, params=None, timeout=None):  # noqa: ANN001
        raise AssertionError("Remote platform should not be called for an unknown local token")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    app = create_bridge_app(
        remote_platform_url="https://example.com",
        session_store_path=tmp_path / "sessions.json",
    )
    client = TestClient(app)

    response = client.post(
        "/api/v2/platform/wxs2mp",
        headers={"Authorization": "Bearer gzh_local_unknown"},
        json={"url": "https://mp.weixin.qq.com/s/test"},
    )

    assert response.status_code == 401
    assert response.json()["message"] == f"WeReadError401: {RECONNECT_MESSAGE}"


def test_bridge_resolves_mp_link_locally_without_remote_platform(monkeypatch, tmp_path: Path) -> None:
    class FakeResolver:
        def resolve(self, url: str) -> list[dict[str, object]]:
            assert url == "https://mp.weixin.qq.com/s/test"
            return [
                {
                    "id": "MP_WXS_2392024520",
                    "name": "APPSO",
                    "cover": "https://wx.qlogo.cn/example/0",
                    "intro": "AI 第一新媒体",
                    "updateTime": 1774661648,
                }
            ]

    async def fake_request(self, method, url, headers=None, json=None, params=None, timeout=None):  # noqa: ANN001
        raise AssertionError("Remote platform should not be called when local resolution succeeds")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    app = create_bridge_app(
        remote_platform_url="https://example.com",
        session_store_path=tmp_path / "sessions.json",
        mp_info_resolver=FakeResolver(),
    )
    client = TestClient(app)

    response = client.post(
        "/api/v2/platform/wxs2mp",
        json={"url": "https://mp.weixin.qq.com/s/test"},
    )

    assert response.status_code == 200
    assert response.json()[0]["id"] == "MP_WXS_2392024520"
    assert response.json()[0]["name"] == "APPSO"


def test_bridge_does_not_invalidate_local_session_on_first_401(monkeypatch, tmp_path: Path) -> None:
    async def fake_request(self, method, url, headers=None, json=None, params=None, timeout=None):  # noqa: ANN001
        return httpx.Response(401, json={"message": "WeReadError401: token expired"})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    session_store = tmp_path / "sessions.json"
    session_store.write_text(
        json.dumps(
            {
                "sessions": {
                    "gzh_local_demo": {
                        "account_id": "1",
                        "account_name": "demo",
                        "remote_token": "remote-token",
                        "status": "active",
                        "last_error": "",
                        "consecutive_auth_failures": 0,
                        "created_at": 1,
                        "updated_at": 1,
                    }
                },
                "updated_at": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    app = create_bridge_app(
        remote_platform_url="https://example.com",
        session_store_path=session_store,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v2/platform/wxs2mp",
        headers={"Authorization": "Bearer gzh_local_demo"},
        json={"url": "https://mp.weixin.qq.com/s/test"},
    )

    assert response.status_code == 401
    assert response.json()["message"] == f"WeReadError401: {SOFT_401_MESSAGE}"

    payload = json.loads(session_store.read_text(encoding="utf-8"))
    record = payload["sessions"]["gzh_local_demo"]
    assert record["status"] == "active"
    assert record["consecutive_auth_failures"] == 1
