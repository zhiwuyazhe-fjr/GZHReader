from __future__ import annotations

import httpx
import pytest

from gzhreader_core.ai import RETRIES, TEMPERATURE, TIMEOUT_SECONDS, Summarizer
from gzhreader_core.credentials import CredentialVault


class FakeClient:
    response = None
    def __init__(self, **kwargs): self.kwargs = kwargs
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, *args, **kwargs): return self.response


def test_fixed_parameters_and_structured_summary(tmp_path):
    assert (TIMEOUT_SECONDS, RETRIES, TEMPERATURE) == (90, 2, 0.2)
    vault = CredentialVault(tmp_path)
    vault.save("ai", {"base_url": "https://example.test/v1", "api_key": "key", "model": "model", "enabled": True})
    FakeClient.response = httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"summary":"摘要","key_points":["一","二","三"],"tags":["主题"],"takeaway":"结论"}\n```'}}]}, request=httpx.Request("POST", "https://example.test"))
    result = Summarizer(vault, client_factory=FakeClient).summarize("标题", "正文", "公众号")
    assert result["status"] == "done"
    assert result["summary"] == "摘要"
    assert result["prompt_version"]


def test_missing_config_uses_fallback(tmp_path):
    result = Summarizer(CredentialVault(tmp_path)).summarize("标题", "这是一段正文", "公众号")
    assert result["status"] == "fallback"
    assert "正文" in result["summary"]


def configured_summarizer(tmp_path, response):
    vault = CredentialVault(tmp_path)
    vault.save("ai", {"base_url": "https://example.test/v1", "api_key": "test-key", "model": "model", "enabled": True})

    class CountingClient(FakeClient):
        calls = 0

        def post(self, *args, **kwargs):
            self.__class__.calls += 1
            return response

    return Summarizer(vault, client_factory=CountingClient), CountingClient


@pytest.mark.parametrize("content", [
    '{}',
    '{"summary":""}',
    '{"summary":{"invalid":"object"}}',
    '{"summary":"摘要","key_points":"不是列表"}',
    '{"summary":"摘要","tags":[{"invalid":"tag"}]}',
    '{"summary":"摘要","takeaway":["不是文本"]}',
])
def test_invalid_structured_output_falls_back_instead_of_storing_corrupt_summary(tmp_path, content):
    response = httpx.Response(200, json={"choices": [{"message": {"content": content}}]}, request=httpx.Request("POST", "https://example.test"))
    summarizer, client = configured_summarizer(tmp_path, response)
    result = summarizer.summarize("标题", "可靠的原文", "公众号")
    assert result["status"] == "fallback"
    assert result["summary"] == "可靠的原文"
    assert client.calls == RETRIES + 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_http_errors_are_not_retried(tmp_path, status):
    response = httpx.Response(status, json={"error": "unavailable"}, request=httpx.Request("POST", "https://example.test"))
    summarizer, client = configured_summarizer(tmp_path, response)
    assert summarizer.summarize("标题", "正文", "公众号")["status"] == "fallback"
    assert client.calls == 1


def test_partial_config_updates_preserve_other_values_and_hidden_key(tmp_path):
    summarizer, _ = configured_summarizer(tmp_path, None)
    summarizer.save({"enabled": False})
    assert summarizer.config() == {"base_url": "https://example.test/v1", "api_key": "test-key", "model": "model", "enabled": False}
    summarizer.save({"model": "other-model", "api_key": None})
    assert summarizer.config()["api_key"] == "test-key"
    assert summarizer.config()["enabled"] is False
    assert "api_key" not in summarizer.public_config()


def test_endpoint_does_not_append_path_twice_and_rejects_invalid_schemes():
    assert Summarizer._endpoint("https://example.test/v1/") == "https://example.test/v1/chat/completions"
    assert Summarizer._endpoint("https://example.test/v1/chat/completions") == "https://example.test/v1/chat/completions"
    with pytest.raises(ValueError):
        Summarizer._endpoint("file:///tmp/model")


def test_connection_test_requires_valid_chat_response(tmp_path):
    response = httpx.Response(200, json={"error": "not a chat endpoint"}, request=httpx.Request("POST", "https://example.test"))
    summarizer, client = configured_summarizer(tmp_path, response)
    assert summarizer.test({})["ok"] is False
    assert client.calls == 1


def test_connection_test_uses_existing_hidden_key_when_payload_key_is_null(tmp_path):
    response = httpx.Response(200, json={"choices": [{"message": {"content": "连接成功"}}]}, request=httpx.Request("POST", "https://example.test"))
    summarizer, client = configured_summarizer(tmp_path, response)
    assert summarizer.test({"api_key": None})["ok"] is True
    assert client.calls == 1
