from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta

import pytest

from gzhreader_core.browser.auth import (
    WeReadLoginCapture,
    build_browser_command,
    classify_articles_payload,
)
from gzhreader_core.credentials import CredentialVault
from gzhreader_core.scheduler import Scheduler
from gzhreader_core.storage import Storage


def test_credentials_round_trip_and_clear(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save("weread", {"cookie": "secret-cookie", "ticket": "secret-ticket"})
    stored = (tmp_path / "weread.bin").read_bytes()
    assert b"secret-cookie" not in stored
    assert vault.load("weread")["ticket"] == "secret-ticket"
    vault.clear("weread")
    assert vault.load("weread") == {}


def test_scheduler_reschedules_and_respects_briefing_skip(tmp_path):
    storage = Storage(tmp_path / "data.db")
    scheduler = Scheduler(storage, lambda: {}, lambda: {}, lambda *_: None)
    storage.update_settings({"refresh_minutes": 15})
    status = scheduler.reschedule(reset=True)
    next_refresh = datetime.fromisoformat(status["next_refresh_at"])
    assert timedelta(minutes=14) < next_refresh - datetime.now().astimezone() < timedelta(minutes=16)
    storage.update_settings({"briefing_time": "00:01", "briefing_skip_day": date.today().isoformat()})
    assert scheduler.briefing_due_now() is False


def test_weread_browser_response_classification():
    assert classify_articles_payload({"reviews": []})[0] == "ready"
    assert classify_articles_payload({"errCode": -2041, "errMsg": "-2041"})[0] == "captcha"
    assert classify_articles_payload({"errCode": -2041, "errMsg": "操作过于频繁，请稍后再试"})[0] == "cooldown"
    assert classify_articles_payload({"errCode": -2010, "errMsg": "user missing"})[0] == "cooldown"
    assert classify_articles_payload({"errCode": -2012, "errMsg": "user missing"})[0] == "login"


def test_verification_flow_does_not_override_browser_window_close():
    source = inspect.getsource(WeReadLoginCapture.run)
    assert "window.close" not in source


def test_manual_verification_browser_uses_minimal_cdp_command(tmp_path):
    command = build_browser_command(
        tmp_path / "msedge.exe",
        tmp_path / "profile",
        32123,
    )

    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=32123" in command
    assert f"--user-data-dir={tmp_path / 'profile'}" in command
    assert "--no-sandbox" not in command
    assert "--disable-extensions" not in command
    assert "--enable-automation" not in command


def test_scheduler_accepts_legacy_naive_refresh_time(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.update_settings({"next_refresh_at": "2000-01-01T00:00:00", "briefing_enabled": False})
    calls = []
    scheduler = Scheduler(storage, lambda: calls.append("sync") or {}, lambda: {}, lambda *_: None)
    scheduler._tick()
    assert calls == ["sync"]
    assert datetime.fromisoformat(storage.settings()["next_refresh_at"]).tzinfo is not None


def test_scheduler_measures_interval_from_completion(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "data.db")
    storage.update_settings({"refresh_minutes": 15, "next_refresh_at": "2000-01-01T00:00:00+00:00", "briefing_enabled": False})
    now = storage.local_now()
    clock = [now]

    def slow_sync():
        clock[0] += timedelta(minutes=20)
        return {}

    scheduler = Scheduler(storage, slow_sync, lambda: {}, lambda *_: None)
    monkeypatch.setattr(scheduler, "_now", lambda settings=None: clock[0])
    monkeypatch.setattr("gzhreader_core.scheduler.random.randint", lambda *_: 0)
    scheduler._tick()
    assert datetime.fromisoformat(storage.settings()["next_refresh_at"]) == (now + timedelta(minutes=35)).replace(microsecond=0)


def test_scheduler_preserves_settings_changed_during_sync(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.update_settings({"next_refresh_at": "2000-01-01T00:00:00+00:00", "briefing_enabled": False})
    scheduler = None

    def pause_during_sync():
        storage.update_settings({"refresh_paused": True})
        scheduler.reschedule()
        return {}

    scheduler = Scheduler(storage, pause_during_sync, lambda: {}, lambda *_: None)
    scheduler._tick()
    assert storage.settings()["next_refresh_at"] == ""


@pytest.mark.parametrize("value", ["25:00", "12:90", "invalid", "12:30:00"])
def test_invalid_legacy_briefing_time_is_not_due(tmp_path, monkeypatch, value):
    storage = Storage(tmp_path / "data.db")
    settings = {**storage.settings(), "briefing_time": value}
    monkeypatch.setattr(storage, "settings", lambda: settings)
    scheduler = Scheduler(storage, lambda: {}, lambda: {}, lambda *_: None)
    assert scheduler.briefing_due_now() is False


def test_failed_briefing_is_retried_with_backoff(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "data.db")
    storage.update_settings({"refresh_minutes": 0, "briefing_time": "00:00"})
    calls = []

    def failed_briefing():
        calls.append("briefing")
        raise RuntimeError("temporarily unavailable")

    scheduler = Scheduler(storage, lambda: {}, failed_briefing, lambda *_: None)
    clock = [storage.local_now()]
    monkeypatch.setattr(scheduler, "_now", lambda settings=None: clock[0])
    with pytest.raises(RuntimeError):
        scheduler._tick()
    scheduler._tick()
    assert calls == ["briefing"]
    clock[0] += timedelta(minutes=6)
    with pytest.raises(RuntimeError):
        scheduler._tick()
    assert calls == ["briefing", "briefing"]


def test_stopped_scheduler_does_not_launch_briefing_after_sync(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.update_settings({"next_refresh_at": "2000-01-01T00:00:00+00:00", "briefing_time": "00:00"})
    calls = []
    scheduler = None

    def stop_during_sync():
        scheduler.stop_event.set()
        return {}

    scheduler = Scheduler(storage, stop_during_sync, lambda: calls.append("briefing"), lambda *_: None)
    scheduler._tick()
    assert calls == []
