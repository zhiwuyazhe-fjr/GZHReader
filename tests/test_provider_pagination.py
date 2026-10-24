from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gzhreader_core.credentials import CredentialVault
from gzhreader_core.models import SourceProfile
from gzhreader_core.providers.weread import WeReadError, WeReadProvider


class FakeClient:
    pages = []
    content = ""
    calls = 0
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, url, params, headers):
        self.__class__.calls += 1
        if url.endswith("/articles"):
            payload = self.pages.pop(0)
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))
        return httpx.Response(200, text=self.content, request=httpx.Request("GET", url))


def review(review_id, ts):
    return {"review": {"reviewId": review_id, "createTime": ts, "mpInfo": {"title": review_id, "originalId": review_id, "time": ts}}}


def test_pagination_uses_group_offsets_and_stops_at_cursor(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save("weread", {"cookie": "a=b", "ticket": "ticket"})
    FakeClient.pages = [
        {"reviews": [{"subReviews": [review("new", 200)]}]},
        {"reviews": [{"subReviews": [review("old", 100)]}]},
    ]
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)
    batch = provider.list_articles(SourceProfile("MP_WXS_1", "公众号"), previous_cursor=100, max_pages=20)
    assert [item.origin_id for item in batch.articles] == ["new"]
    assert batch.reached_cursor
    assert batch.pages_scanned == 2


def test_auth_error_is_classified(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save("weread", {"cookie": "a=b", "ticket": "ticket"})
    FakeClient.pages = [{"errCode": -2041, "errMsg": "expired"}]
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)
    try:
        provider.list_articles(SourceProfile("MP_WXS_1", "公众号"))
    except WeReadError as exc:
        assert exc.reconnect
        assert exc.verification_required
        assert not exc.cooldown
    else:
        raise AssertionError("auth error was not raised")


def test_risk_control_error_uses_24_hour_cooldown(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save("weread", {"cookie": "a=b", "ticket": "ticket"})
    FakeClient.pages = [{"errCode": -2041, "errMsg": "操作过于频繁，请稍后再试"}]
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)
    try:
        provider.list_articles(SourceProfile("MP_WXS_1", "公众号"))
    except WeReadError as exc:
        assert exc.reconnect
        assert exc.cooldown
        assert exc.cooldown_minutes == 24 * 60
    else:
        raise AssertionError("risk control error was not raised")


def test_current_wpa_header_is_preserved():
    headers = WeReadProvider._headers(
        "wr_vid=1; wr_skey=skey",
        "signed-value",
        True,
        auth_header_name="x-wrpa-0",
        user_agent="Browser UA",
        referer="https://weread.qq.com/web/mp/reader/encoded",
    )
    assert headers["x-wrpa-0"] == "signed-value"
    assert headers["User-Agent"] == "Browser UA"
    assert headers["Referer"].endswith("/web/mp/reader/encoded")
    assert "x-wr-ticket" not in headers


def test_captcha_headers_include_randstr():
    headers = WeReadProvider._headers(
        "wr_vid=1; wr_skey=skey",
        "captcha-ticket",
        True,
        auth_header_name="x-wr-ticket",
        randstr="captcha-randstr",
    )
    assert headers["x-wr-ticket"] == "captcha-ticket"
    assert headers["x-wr-randstr"] == "captcha-randstr"


def test_prefetched_browser_page_avoids_immediate_http_replay(tmp_path):
    vault = CredentialVault(tmp_path)
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)
    provider.cache_articles_page(
        "MP_WXS_1",
        0,
        {"reviews": [{"subReviews": [review("browser-result", 300)]}]},
    )
    batch = provider.list_articles(SourceProfile("MP_WXS_1", "Account"), limit=1)
    assert [item.origin_id for item in batch.articles] == ["browser-result"]
    assert batch.pages_scanned == 1


def test_consumed_browser_authorization_is_not_replayed(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save(
        "weread",
        {
            "cookie": "wr_vid=1; wr_skey=skey",
            "ticket": "already-used-ticket",
            "authorization_consumed": True,
        },
    )
    FakeClient.calls = 0
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)

    try:
        provider.list_articles(SourceProfile("MP_WXS_1", "公众号"))
    except WeReadError as exc:
        assert exc.verification_required
    else:
        raise AssertionError("consumed browser authorization was replayed")

    assert FakeClient.calls == 0


def consumed_browser_provider(tmp_path, entries):
    vault = CredentialVault(tmp_path)
    vault.save("weread", {"cookie": "a=b", "ticket": "used-ticket", "authorization_consumed": True})
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)
    provider.cache_articles_page("MP_WXS_1", 0, {"reviews": [{"subReviews": entries}]})
    FakeClient.calls = 0
    return provider


def test_first_browser_import_saves_short_page_without_replaying_authorization(tmp_path):
    provider = consumed_browser_provider(tmp_path, [review("browser-new", 300)])
    batch = provider.list_articles(SourceProfile("MP_WXS_1", "公众号"), limit=20)
    assert [item.origin_id for item in batch.articles] == ["browser-new"]
    assert batch.newest_cursor == 300
    assert batch.pages_scanned == 1
    assert FakeClient.calls == 0


def test_incremental_browser_page_requires_verification_if_old_cursor_not_reached(tmp_path):
    provider = consumed_browser_provider(tmp_path, [review("browser-new", 300)])
    with pytest.raises(WeReadError) as raised:
        provider.list_articles(SourceProfile("MP_WXS_1", "公众号"), previous_cursor=100)
    assert raised.value.verification_required
    assert FakeClient.calls == 0


def test_incremental_browser_page_succeeds_when_old_cursor_is_reached(tmp_path):
    provider = consumed_browser_provider(tmp_path, [review("browser-new", 300), review("old", 100)])
    batch = provider.list_articles(SourceProfile("MP_WXS_1", "公众号"), previous_cursor=100)
    assert [item.origin_id for item in batch.articles] == ["browser-new"]
    assert batch.newest_cursor == 300
    assert batch.reached_cursor
    assert FakeClient.calls == 0


def test_first_sync_continues_across_pages_and_deduplicates(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save("weread", {"cookie": "a=b", "ticket": "ticket"})
    FakeClient.pages = [
        {"reviews": [{"subReviews": [review("new", 300)]}]},
        {"reviews": [{"subReviews": [review("new", 300), review("second", 200)]}]},
        {"reviews": []},
    ]
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)
    batch = provider.list_articles(SourceProfile("MP_WXS_1", "公众号"))
    assert [item.origin_id for item in batch.articles] == ["new", "second"]
    assert batch.pages_scanned == 3
    assert batch.newest_cursor == 300
    assert batch.reached_cursor


@pytest.mark.parametrize("payload", [[], {}, {"reviews": "bad"}, {"reviews": ["bad"]}, {"reviews": [{"subReviews": [{"review": ["bad"]}]}]}])
def test_malformed_payload_is_not_treated_as_empty_history(tmp_path, payload):
    vault = CredentialVault(tmp_path)
    vault.save("weread", {"cookie": "a=b", "ticket": "ticket"})
    FakeClient.pages = [payload]
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)
    with pytest.raises(WeReadError) as raised:
        provider.list_articles(SourceProfile("MP_WXS_1", "公众号"))
    assert raised.value.code == "invalid_response"


def test_invalid_article_timestamp_does_not_fabricate_new_cursor(tmp_path):
    provider = WeReadProvider(CredentialVault(tmp_path), sleep=lambda _: None)
    provider.cache_articles_page("MP_WXS_1", 0, {"reviews": [{"subReviews": [review("bad-date", "invalid")]}]})
    with pytest.raises(WeReadError) as raised:
        provider.list_articles(SourceProfile("MP_WXS_1", "公众号"))
    assert raised.value.code == "invalid_response"


def test_incomplete_incremental_backlog_does_not_advance_cursor(tmp_path):
    provider = WeReadProvider(CredentialVault(tmp_path), sleep=lambda _: None)
    provider.cache_articles_page("MP_WXS_1", 0, {"reviews": [{"subReviews": [review("new", 300)]}]})
    with pytest.raises(WeReadError) as raised:
        provider.list_articles(SourceProfile("MP_WXS_1", "公众号"), previous_cursor=100, max_pages=1)
    assert raised.value.code == "backlog_incomplete"


def test_incremental_catchup_crosses_soft_limit_without_losing_articles(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save("weread", {"cookie": "a=b", "ticket": "ticket"})
    entries = [review(f"article-{timestamp}", timestamp) for timestamp in range(301, 99, -1)]
    FakeClient.pages = [
        {"reviews": [{"subReviews": entries[start:start + 50]}]}
        for start in range(0, len(entries), 50)
    ]
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)
    batch = provider.list_articles(SourceProfile("MP_WXS_1", "公众号"), limit=200, previous_cursor=100)
    assert len(batch.articles) == 201
    assert batch.articles[-1].origin_id == "article-101"
    assert batch.newest_cursor == 301
    assert batch.reached_cursor
    assert batch.pages_scanned == 5


def test_incremental_catchup_remains_bounded_to_twenty_pages(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save("weread", {"cookie": "a=b", "ticket": "ticket"})
    FakeClient.pages = [
        {"reviews": [{"subReviews": [review(f"article-{index}", 300 - index)]}]}
        for index in range(21)
    ]
    provider = WeReadProvider(vault, client_factory=FakeClient, sleep=lambda _: None)
    with pytest.raises(WeReadError) as raised:
        provider.list_articles(SourceProfile("MP_WXS_1", "公众号"), previous_cursor=100, limit=1, max_pages=20)
    assert raised.value.code == "backlog_incomplete"
    assert len(FakeClient.pages) == 1
