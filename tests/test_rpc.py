from __future__ import annotations

import io
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from gzhreader_core import __version__
from gzhreader_core.paths import AppPaths
from gzhreader_core.rpc import CoreApp, RpcError, RpcServer


class EchoApp:
    def __init__(self, emit):
        self.closed = False

    def dispatch(self, method, params):
        if method == "missing":
            raise RpcError(-32601, "不支持的操作")
        if method == "explode":
            raise RuntimeError("private internal exception")
        return params

    def shutdown(self):
        self.closed = True


def run_rpc(monkeypatch, lines):
    output = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines)))
    monkeypatch.setattr("sys.stdout", output)
    server = RpcServer(EchoApp)
    server.run()
    return server, [json.loads(line) for line in output.getvalue().splitlines()]


def test_rpc_validates_envelope_and_params_and_recovers_for_next_request(monkeypatch):
    server, responses = run_rpc(monkeypatch, [
        "{invalid JSON",
        "[]",
        "null",
        '{"jsonrpc":"2.0","method":"echo","id":true}',
        '{"jsonrpc":"2.0","method":"echo","params":[],"id":1}',
        '{"jsonrpc":"2.0","method":"missing","id":2}',
        '{"jsonrpc":"2.0","method":"echo","params":{"value":"正确"},"id":3}',
    ])
    assert responses[0] == {"jsonrpc": "2.0", "event": "core.ready", "payload": {"version": __version__}}
    assert [row["error"]["code"] for row in responses[1:-1]] == [-32700, -32600, -32600, -32600, -32602, -32601]
    assert responses[-1]["result"] == {"value": "正确"}
    assert server.app.closed


def test_rpc_does_not_reply_to_notifications_but_replies_to_explicit_null_id(monkeypatch):
    _, responses = run_rpc(monkeypatch, [
        '{"jsonrpc":"2.0","method":"echo"}',
        '{"jsonrpc":"2.0","method":"missing"}',
        '{"jsonrpc":"2.0","method":"echo","params":[]}',
        '{"jsonrpc":"2.0","method":"echo","id":null}',
    ])
    assert len(responses) == 2
    assert responses[1] == {"jsonrpc": "2.0", "id": None, "result": {}}


def test_rpc_internal_errors_do_not_expose_exception_details(monkeypatch):
    _, responses = run_rpc(monkeypatch, ['{"jsonrpc":"2.0","method":"explode","id":1}'])
    assert responses[-1]["error"]["code"] == -32000
    assert "private" not in responses[-1]["error"]["message"]


def test_rpc_always_shuts_down_app_when_output_stream_is_closed(monkeypatch):
    class ClosedOutput(io.StringIO):
        def write(self, text):
            raise BrokenPipeError()

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdout", ClosedOutput())
    server = RpcServer(EchoApp)
    with pytest.raises(BrokenPipeError):
        server.run()
    assert server.app.closed


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("gzhreader_core.rpc.configure_logging", lambda *_: None)
    paths = AppPaths(
        root=tmp_path,
        db=tmp_path / "data.db",
        logs=tmp_path / "logs",
        briefings=tmp_path / "briefings",
        browser_profile=tmp_path / "browser",
        link_browser_profile=tmp_path / "link-browser",
        secrets=tmp_path / "secrets",
        backups=tmp_path / "backups",
    )
    value = CoreApp(lambda *_: None, paths=paths, start_scheduler=False)
    yield value
    value.shutdown()


def test_article_pagination_is_forwarded_without_changing_list_contract(app, monkeypatch):
    received = []
    monkeypatch.setattr(app.storage, "articles", lambda **kwargs: received.append(kwargs) or [])
    assert app.dispatch("articles.list", {"limit": 51, "offset": 100}) == []
    assert received[0]["limit"] == 51
    assert received[0]["offset"] == 100


@pytest.mark.parametrize("params", [{"offset": -1}, {"offset": True}, {"offset": "2"}, {"limit": 0}, {"limit": 1.5}, {"unread_only": "false"}])
def test_article_pagination_and_filter_types_are_validated(app, params):
    with pytest.raises(ValueError):
        app.dispatch("articles.list", params)


def test_settings_response_contains_new_schedule_and_failed_settings_do_not_change_ai(app):
    response = app.dispatch("settings.update", {"settings": {"refresh_minutes": 15}})
    assert response["next_refresh_at"]
    assert response["next_refresh_at"] == app.storage.settings()["next_refresh_at"]
    with pytest.raises(ValueError):
        app.dispatch("settings.update", {"settings": {"refresh_minutes": 17, "ai": {"model": "other-model"}}})
    assert app.summarizer.config() == {}


def test_sync_status_is_available_in_bootstrap_and_scheduler_status(app):
    assert app.bootstrap()["scheduler"]["syncing"] is False
    app.reader._sync_lock.acquire()
    try:
        assert app.dispatch("scheduler.status", {})["syncing"] is True
        assert app.dispatch("app.health", {})["scheduler"]["syncing"] is True
    finally:
        app.reader._sync_lock.release()


def test_naive_legacy_cooldown_does_not_break_health(app):
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    app.storage.set_connection_state("weread", "cooldown", "稍后再试", True, until.isoformat())
    assert app.dispatch("app.health", {})["connection"]["state"] == "cooldown"


def test_duplicate_auth_is_rejected_and_shutdown_ignores_late_success(app):
    entered, cancelled = threading.Event(), threading.Event()

    class DelayedLogin:
        def prepare(self):
            cancelled.clear()

        def run(self, *_):
            entered.set()
            assert cancelled.wait(5)
            return {"ok": True}

        def cancel(self):
            cancelled.set()

    app.login = DelayedLogin()
    try:
        assert app._background_auth("MP_WXS_1")["accepted"] is True
        assert entered.wait(5)
        assert app._background_auth("MP_WXS_1")["accepted"] is False
    finally:
        app.shutdown()
    assert app.storage.connection_state("weread") is None
    assert app._background_auth("MP_WXS_1")["accepted"] is False
    assert app._background_sync()["accepted"] is False
    app.shutdown()
