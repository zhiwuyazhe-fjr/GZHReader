from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from gzhreader_core.briefing import BriefingService


class BriefingStorage:
    def __init__(self, articles):
        self.articles = articles
        self.saved = []

    def day_articles(self, day):
        return self.articles

    def save_briefing(self, *args):
        self.saved.append(args)

    def local_now(self):
        return datetime(2026, 10, 24, 21, 30, tzinfo=timezone.utc)


class RecordingSummarizer:
    def __init__(self):
        self.inputs = []

    def summarize(self, title, content, source):
        self.inputs.append(content)
        return {"summary": f"概览 {len(self.inputs)}"}


def article(index, summary="文章摘要"):
    return {
        "source_name": "公众号",
        "title": f"文章 {index}",
        "url": f"https://example.test/{index}",
        "summary": summary,
        "digest": "",
        "takeaway": "文章结论",
        "tags": ["技术"],
    }


def test_briefing_chunks_by_prompt_size_before_final_overview(tmp_path):
    articles = [article(index, "长摘要" * 500) for index in range(30)]
    storage = BriefingStorage(articles)
    summarizer = RecordingSummarizer()
    result = BriefingService(storage, tmp_path, summarizer).generate(date(2026, 10, 24))
    assert len(summarizer.inputs) > 1
    assert all(len(text) <= 10000 for text in summarizer.inputs)
    assert any("文章 29" in text for text in summarizer.inputs)
    assert result["article_count"] == 30
    assert result["themes"] == ["技术"]
    assert Path(result["file_path"]).read_text(encoding="utf-8") == result["markdown"]


def test_failed_briefing_export_preserves_previous_file_and_cleans_temporary_file(tmp_path, monkeypatch):
    storage = BriefingStorage([article(1)])
    service = BriefingService(storage, tmp_path, RecordingSummarizer())
    first = service.generate(date(2026, 10, 24))
    original = Path.replace

    def fail_export(path, target):
        if path.suffix == ".tmp":
            raise OSError("destination is locked")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", fail_export)
    with pytest.raises(OSError):
        service.generate(date(2026, 10, 24))
    assert Path(first["file_path"]).read_text(encoding="utf-8") == first["markdown"]
    assert len(storage.saved) == 1
    assert list(tmp_path.glob("*.tmp")) == []


def test_briefing_escapes_article_link_labels_and_parenthesized_urls(tmp_path):
    value = article(1)
    value.update(title="标题 [补充]\n另起一行", url="https://example.test/path_(version)")
    result = BriefingService(BriefingStorage([value]), tmp_path, RecordingSummarizer()).generate(date(2026, 10, 24))
    assert "[标题 \\[补充\\] 另起一行](<https://example.test/path_(version)>)" in result["markdown"]


def test_empty_day_does_not_request_ai_summary(tmp_path):
    summarizer = RecordingSummarizer()
    result = BriefingService(BriefingStorage([]), tmp_path, summarizer).generate(date(2026, 10, 24))
    assert result["overview"] == "今天没有新增文章。"
    assert result["article_count"] == 0
    assert summarizer.inputs == []
