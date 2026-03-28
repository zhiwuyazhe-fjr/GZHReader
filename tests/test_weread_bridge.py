from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from gzhreader.weread_bridge import LOCAL_TOKEN_PREFIX, RECONNECT_MESSAGE, create_bridge_app


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
