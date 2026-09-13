from __future__ import annotations

from datetime import datetime, timezone

from gzhreader_core.models import SourceProfile
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
