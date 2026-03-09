from gzhreader.config import LLMConfig
from gzhreader.summarizer import OpenAICompatibleSummarizer, SummarizeInput, resolve_api_key


def test_summarizer_falls_back_without_api_key() -> None:
    summarizer = OpenAICompatibleSummarizer(LLMConfig(api_key=""))
    output = summarizer.summarize(SummarizeInput(title="标题", content="这是一段很短的正文。", author="作者"))
    assert "这是一段很短的正文" in output


def test_resolve_api_key_prefers_config_over_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    api_key, source = resolve_api_key(LLMConfig(api_key="config-secret"))

    assert api_key == "config-secret"
    assert source == "config"


def test_resolve_api_key_uses_env_when_config_missing(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    api_key, source = resolve_api_key(LLMConfig(api_key=""))

    assert api_key == "env-secret"
    assert source == "env"
