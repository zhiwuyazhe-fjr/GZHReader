from __future__ import annotations

from gzhreader_core.credentials import CredentialVault
from gzhreader_core.models import SourceProfile
from gzhreader_core.providers import WeReadError
from gzhreader_core.service import ReaderService
from gzhreader_core.storage import Storage


class FailedProvider:
    def list_articles(self, source, **kwargs):
        raise WeReadError("network_error", "网络连接失败")
    def mark_error(self, error): pass


class Dummy:
    pass


def test_failed_sync_does_not_advance_cursor(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    service = ReaderService(storage, FailedProvider(), Dummy(), Dummy(), Dummy(), sleep=lambda _: None)
    result = service.sync(lambda *_: None, force=True)
    source = storage.source("MP_WXS_1")
    assert result["errors"]
    assert source["sync_cursor"] == 0
    assert source["next_attempt_at"]


class RiskProvider:
    def __init__(self, vault):
        self.vault = vault

    def list_articles(self, source, **kwargs):
        raise WeReadError(
            -2041,
            "微信读书操作过于频繁，请 24 小时后再重新连接",
            reconnect=True,
            cooldown=True,
            cooldown_minutes=24 * 60,
        )

    def mark_error(self, error):
        pass


def test_risk_control_preserves_credentials_and_sets_cooldown(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    vault = CredentialVault(tmp_path / "secrets")
    vault.save("weread", {"cookie": "secret", "ticket": "ticket"})
    service = ReaderService(storage, RiskProvider(vault), Dummy(), Dummy(), Dummy(), sleep=lambda _: None)

    result = service.sync(lambda *_: None, force=True)

    assert result["errors"]
    assert vault.load("weread") == {"cookie": "secret", "ticket": "ticket"}
    assert storage.connection_state("weread")["state"] == "cooldown"
    source = storage.source("MP_WXS_1")
    assert source["status"] == "limited"
    assert source["cooldown_until"]


class VerificationProvider:
    def __init__(self, vault):
        self.vault = vault
        self.calls = 0

    def list_articles(self, source, **kwargs):
        self.calls += 1
        raise WeReadError(
            -2041,
            "访问授权已过期，请在浏览器中重新验证",
            reconnect=True,
            verification_required=True,
        )

    def mark_error(self, error):
        pass


def test_verification_required_preserves_session_and_blocks_replay(tmp_path):
    storage = Storage(tmp_path / "data.db")
    storage.upsert_source(SourceProfile("MP_WXS_1", "公众号"))
    vault = CredentialVault(tmp_path / "secrets")
    vault.save("weread", {"cookie": "secret", "ticket": "stale-ticket"})
    provider = VerificationProvider(vault)
    service = ReaderService(storage, provider, Dummy(), Dummy(), Dummy(), sleep=lambda _: None)
    events = []

    first = service.sync(lambda event, payload: events.append((event, payload)), force=True)
    second = service.sync(lambda event, payload: events.append((event, payload)), force=True)

    assert first["errors"]
    assert not second["errors"]
    assert provider.calls == 1
    assert vault.load("weread") == {"cookie": "secret", "ticket": "stale-ticket"}
    assert storage.connection_state("weread")["state"] == "verification"
    assert any(event == "verification.required" for event, _payload in events)
    assert any(event == "sync.skipped" for event, _payload in events)
