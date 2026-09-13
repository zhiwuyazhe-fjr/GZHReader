from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta

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
