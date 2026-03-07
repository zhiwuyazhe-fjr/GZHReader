from gzhreader.config import LLMConfig
from gzhreader.summarizer import OpenAICompatibleSummarizer, SummarizeInput


def test_summarizer_falls_back_without_api_key() -> None:
    summarizer = OpenAICompatibleSummarizer(LLMConfig(api_key=""))
    output = summarizer.summarize(SummarizeInput(title="标题", content="这是一段很短的正文。", author="作者"))
    assert "这是一段很短的正文" in output
