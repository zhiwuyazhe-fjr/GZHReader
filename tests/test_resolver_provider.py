from __future__ import annotations

from datetime import datetime, timezone
import sys
from types import ModuleType
from unittest.mock import MagicMock

import httpx
import pytest

from gzhreader_core.models import SourceProfile
from gzhreader_core.article_fetcher import ArticleContentFetcher, BrowserCandidate
from gzhreader_core.browser.auth import WeReadLoginCapture, classify_articles_payload
from gzhreader_core.credentials import CredentialVault
from gzhreader_core.fetch_config import ArticleFetchConfig, RSSConfig
from gzhreader_core.providers.resolver import ArticleLinkResolver
from gzhreader_core.providers.weread import (
    build_mp_reader_url,
    build_mp_url,
    encode_weread_id,
    extract_mp_content,
    parse_mp_articles,
)


def test_weread_reader_id_encoding_and_mp_url():
    assert encode_weread_id("41598972") == "c9d327e0727abffcc9d74ab"
    assert encode_weread_id("MP_WXS_3916483328") == "3634290224d505f5758535f333931363438333332384b2"
    assert build_mp_reader_url("MP_WXS_3916483328") == (
        "https://weread.qq.com/web/mp/reader/3634290224d505f5758535f333931363438333332384b2"
    )


def test_biz_decode_and_metadata_extract():
    resolver = ArticleLinkResolver()
    assert resolver.decode_biz("Mzg5Mjc3MjIyMA==") == "3892772220"
    html = """
    <html><head><meta property="og:image" content="https://img/cover.jpg"></head>
    <body><h1 id="activity-name">文章标题</h1><strong id="js_name">测试公众号</strong>
    <script>var biz = 'Mzg5Mjc3MjIyMA=='; var ct = '1760000000'; var ori_head_img_url = 'https://img/avatar.jpg';</script></body></html>
    """
    value = resolver._extract(html, "https://mp.weixin.qq.com/s?mid=1&idx=2&sn=3")
    assert value["name"] == "测试公众号"
    assert value["title"] == "文章标题"
    assert value["article_id"] == "1-2-3"




def test_extracts_modern_metadata_and_embedded_biz():
    resolver = ArticleLinkResolver()
    html = """
    <html><head>
      <meta property="og:title" content="Modern article">
      <meta property="og:article:author" content="Modern account">
      <meta property="og:image" content="https://img/cover.jpg">
      <meta name="description" content="Account description">
    </head><body>
      <a href="https://mp.weixin.qq.com/s?__biz=Mzg5Mjc3MjIyMA%3D%3D&amp;mid=12&amp;idx=1">source</a>
    </body></html>
    """
    value = resolver._extract(html, "https://mp.weixin.qq.com/s/short-id")
    assert value["biz"] == "Mzg5Mjc3MjIyMA=="
    assert value["name"] == "Modern account"
    assert value["title"] == "Modern article"
    assert value["intro"] == "Account description"


def test_metadata_ignores_embedded_nickname_attributes_and_script_fragments():
    resolver = ArticleLinkResolver()
    html = """
    <html><head>
      <meta property="og:article:author" content="权威公众号">
      <meta property="og:title" content="权威标题">
    </head><body>
      <a data-miniprogram-nickname="别的小程序">embedded</a>
      <script>
        var nickname = '' || '';
        var embedded = '<span data-miniprogram-nickname="别的公众号">card</span>';
        var msg_title = '真实标题'.html(false);
        var biz = 'Mzg5Mjc3MjIyMA==';
      </script>
    </body></html>
    """

    value = resolver._extract(html, "https://mp.weixin.qq.com/s/example")

    assert value["name"] == "权威公众号"
    assert value["title"] == "权威标题"


def test_metadata_uses_only_profile_card_matching_article_biz():
    resolver = ArticleLinkResolver()
    html = """
    <html><body>
      <mp-common-profile data-id="MzIyMzA5NjEyMA==" data-nickname="别的公众号"></mp-common-profile>
      <mp-common-profile
        data-id="MzIzNjc1NzUzMw=="
        data-nickname="量子位"
        data-signature="追踪人工智能新趋势，关注科技行业新突破"
        data-headimg="https://img/qbit.png">
      </mp-common-profile>
      <script>
        var biz = 'MzIzNjc1NzUzMw==';
        var nickname = '量子位\" data-alias=\"QbitAI\" data-from=\"0';
        var msg_title = '刚刚，GPT-6正式发布！'.html(false);
      </script>
    </body></html>
    """

    value = resolver._extract(html, "https://mp.weixin.qq.com/s/example")

    assert value["name"] == "量子位"
    assert value["intro"] == "追踪人工智能新趋势，关注科技行业新突破"
    assert value["avatar"] == "https://img/qbit.png"
    assert value["title"] == "刚刚，GPT-6正式发布！"


def test_captcha_response_triggers_browser_fallback(monkeypatch):
    resolver = ArticleLinkResolver()
    captcha_html = '<html><script>var poc_token = "token";</script><div>\u8bf7\u5b8c\u6210\u9a8c\u8bc1</div></html>'
    article_html = """
    <html><body><h1 id="activity-name">Article</h1><strong id="js_name">Test account</strong>
    <script>var biz = 'Mzg5Mjc3MjIyMA==';</script></body></html>
    """
    browser_calls = []

    class FakeResponse:
        text = captcha_html
        url = "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=token"

        @staticmethod
        def raise_for_status():
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @staticmethod
        def get(url):
            return FakeResponse()

    def browser_fetch(url):
        browser_calls.append(url)
        return article_html, url

    monkeypatch.setattr("gzhreader_core.providers.resolver.httpx.Client", FakeClient)
    monkeypatch.setattr(resolver, "_browser_fetch", browser_fetch)

    result = resolver.resolve_source("https://mp.weixin.qq.com/s/example")
    assert browser_calls == ["https://mp.weixin.qq.com/s/example"]
    assert result["id"] == "MP_WXS_3892772220"
    assert result["name"] == "Test account"


def test_challenge_detection():
    assert ArticleLinkResolver._is_challenge("", "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=x")
    assert ArticleLinkResolver._is_challenge("<div>\u8bf7\u5b8c\u6210\u9a8c\u8bc1</div>", "https://mp.weixin.qq.com/s/a")
    assert not ArticleLinkResolver._is_challenge("<h1>normal article</h1>", "https://mp.weixin.qq.com/s/a")


def test_parse_weread_articles_and_content():
    source = SourceProfile("MP_WXS_1", "测试公众号")
    payload = {"reviews": [{"createTime": 1770000000, "subReviews": [{"review": {"reviewId": "r1", "createTime": 1770000001, "mpInfo": {"title": "标题", "originalId": "abc~def", "pic_url": "cover", "content": "导语", "time": 1770000002}}}]}]}
    articles, groups = parse_mp_articles(payload, source)
    assert groups == 1
    assert articles[0].origin_id == "r1"
    assert articles[0].url == build_mp_url("abc~def")
    assert articles[0].digest == "导语"
    assert extract_mp_content('<div id="js_content"><p>第一段</p><script>bad()</script><p>第二段</p></div>') == "第一段\n第二段"


def test_js_unicode_escapes_preserve_chinese_and_emoji():
    assert ArticleLinkResolver._clean_js_string(r"中文\u540d\x26称") == "中文名&称"
    assert ArticleLinkResolver._clean_js_string(r"中文\ud83d\ude00") == "中文😀"
    assert ArticleLinkResolver._clean_js_string(r"https:\/\/example.com?a=1\x26b=2") == "https://example.com?a=1&b=2"


def test_short_wechat_article_is_valid_fulltext():
    fetcher = ArticleContentFetcher(ArticleFetchConfig(), RSSConfig())
    result = fetcher._extract_from_html(
        '<h1 id="activity-name">短文章</h1><div id="js_content"><p>一篇只有一小段正文的短文章。</p></div>',
        "https://mp.weixin.qq.com/s/short", content_source="http_fulltext",
        fallback_title="", fallback_author="", fallback_publish_time=None,
    )
    assert result.success
    assert result.title == "短文章"
    assert result.content_text == "一篇只有一小段正文的短文章。"


def test_wechat_challenge_cannot_be_saved_as_generic_fulltext():
    fetcher = ArticleContentFetcher(ArticleFetchConfig(), RSSConfig())
    result = fetcher._extract_from_html(
        "<html><body><h1>请完成验证</h1><p>" + "环境异常，请完成验证。" * 50 + "</p></body></html>",
        "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha", content_source="http_fulltext",
        fallback_title="", fallback_author="", fallback_publish_time=None,
    )
    assert not result.success
    assert result.content_source == "fetch_failed"


def test_http_wechat_redirect_to_other_host_cannot_be_saved_as_fulltext(monkeypatch):
    original_client = httpx.Client
    requested_urls = []

    def respond(request):
        requested_urls.append(str(request.url))
        if request.url.host == "mp.weixin.qq.com":
            return httpx.Response(302, headers={"Location": "https://security.example.com/verification"})
        return httpx.Response(200, text="<article>" + "请完成访问验证。" * 60 + "</article>")

    monkeypatch.setattr(
        "gzhreader_core.article_fetcher.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    fetcher = ArticleContentFetcher(ArticleFetchConfig(), RSSConfig())
    result = fetcher._fetch_via_http(
        "https://mp.weixin.qq.com/s/original", fallback_title="", fallback_author="", fallback_publish_time=None,
    )
    assert requested_urls == ["https://mp.weixin.qq.com/s/original", "https://security.example.com/verification"]
    assert not result.success
    assert result.content_source == "fetch_failed"


def test_browser_wechat_redirect_to_other_host_cannot_be_saved_as_fulltext(monkeypatch):
    context = MagicMock()
    page = context.pages.__getitem__.return_value
    page.url = "https://security.example.com/verification"
    page.content.return_value = "<article>" + "请完成访问验证。" * 60 + "</article>"
    manager = MagicMock()
    manager.__enter__.return_value.chromium.launch_persistent_context.return_value = context
    fake_sync_api = ModuleType("playwright.sync_api")
    fake_sync_api.Error = RuntimeError
    fake_sync_api.sync_playwright = lambda: manager
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    fetcher = ArticleContentFetcher(ArticleFetchConfig(), RSSConfig())
    monkeypatch.setattr(fetcher, "_iter_browser_candidates", lambda: iter([BrowserCandidate("chrome", None)]))
    result = fetcher._fetch_via_browser(
        "https://mp.weixin.qq.com/s/original", fallback_title="", fallback_author="", fallback_publish_time=None,
    )
    page.goto.assert_called_once()
    assert not result.success
    assert result.content_source == "fetch_failed"
    context.close.assert_called_once()


def test_generic_article_redirect_still_extracts_fulltext(monkeypatch):
    original_client = httpx.Client
    article_text = "这是一篇可以正常阅读和保存的通用网页文章。" * 30

    def respond(request):
        if request.url.path == "/old":
            return httpx.Response(302, headers={"Location": "https://example.com/new"})
        return httpx.Response(200, text=f"<html><head><title>正常文章</title></head><body><article>{article_text}</article></body></html>")

    monkeypatch.setattr(
        "gzhreader_core.article_fetcher.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    fetcher = ArticleContentFetcher(ArticleFetchConfig(), RSSConfig())
    result = fetcher._fetch_via_http(
        "https://example.com/old", fallback_title="", fallback_author="", fallback_publish_time=None,
    )
    assert result.success
    assert article_text in result.content_text


def test_browser_auth_does_not_accept_malformed_article_list():
    assert classify_articles_payload({"reviews": {}})[0] == "waiting"
    assert classify_articles_payload({"reviews": None})[0] == "waiting"


def test_browser_auth_cancel_before_worker_starts_is_preserved(tmp_path):
    capture = WeReadLoginCapture(tmp_path / "profile", CredentialVault(tmp_path / "vault"))
    capture.prepare()
    capture.cancel()
    with pytest.raises(RuntimeError, match="已取消连接"):
        capture.run("MP_WXS_1", lambda *_: None)
    assert not capture._running.locked()


def test_browser_auth_progress_failure_does_not_lock_future_attempts(tmp_path, monkeypatch):
    fake_playwright = ModuleType("playwright")
    fake_sync_api = ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: None
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    capture = WeReadLoginCapture(tmp_path / "profile", CredentialVault(tmp_path / "vault"))

    def fail(*_):
        raise RuntimeError("progress failed")

    with pytest.raises(RuntimeError, match="progress failed"):
        capture.run("MP_WXS_1", fail)
    assert not capture._running.locked()


def test_browser_auth_only_caches_requested_source_first_page():
    matches = WeReadLoginCapture._is_articles_request
    assert matches("https://weread.qq.com/web/mp/articles?bookId=MP_WXS_1&offset=0", "MP_WXS_1")
    assert not matches("https://weread.qq.com/web/mp/articles?bookId=MP_WXS_2&offset=0", "MP_WXS_1")
    assert not matches("https://weread.qq.com/web/mp/articles?bookId=MP_WXS_1&offset=20", "MP_WXS_1")


def test_malformed_credential_payload_does_not_escape_as_a_list(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save("weread", ["unexpected"])
    assert vault.load("weread") == {}
