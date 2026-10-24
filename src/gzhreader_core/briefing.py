from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import tempfile
import threading


class BriefingService:
    def __init__(self, storage, briefing_dir: Path, summarizer):
        self.storage = storage
        self.dir = briefing_dir
        self.summarizer = summarizer
        self._generate_lock = threading.Lock()

    def generate(self, day: date) -> dict:
        with self._generate_lock:
            return self._generate(day)

    def _generate(self, day: date) -> dict:
        articles = self.storage.day_articles(day)
        grouped: dict[str, list[dict]] = defaultdict(list)
        for article in articles:
            grouped[article["source_name"]].append(article)
        digest_lines = [
            f"- {article['source_name']}《{article['title']}》：{(article['summary'] or article['digest'])[:1500]}"
            for article in articles
        ]
        overview = "今天没有新增文章。"
        if articles:
            while len("\n".join(digest_lines)) > 10000:
                chunks: list[str] = []
                current: list[str] = []
                size = 0
                for line in digest_lines:
                    if current and size + len(line) + 1 > 10000:
                        chunks.append("\n".join(current))
                        current, size = [], 0
                    current.append(line[:10000])
                    size += len(current[-1]) + 1
                if current:
                    chunks.append("\n".join(current))
                digest_lines = [
                    self.summarizer.summarize(f"第 {index + 1} 组文章小结", chunk, "GZHReader")["summary"][:1800]
                    for index, chunk in enumerate(chunks)
                ]
            digest = "\n".join(digest_lines)
            overview = self.summarizer.summarize(f"{day.isoformat()} 每日简报", digest, "GZHReader")["summary"]
        tag_counts = Counter(tag for article in articles for tag in article.get("tags", []))
        themes = [name for name, _count in tag_counts.most_common(5)]
        lines = [
            f"# 公众号每日简报 · {day.isoformat()}",
            "",
            "## 今日概览",
            "",
            overview,
            "",
            f"共收录 {len(articles)} 篇文章。",
            "",
            "## 主要主题",
            "",
            "、".join(themes) if themes else "暂无明确主题",
            "",
            "## 今日必读 Top 3",
            "",
        ]
        for article in articles[:3]:
            lines += [
                self._article_heading(article),
                article["takeaway"] or article["summary"] or article["digest"] or "等待整理",
                "",
            ]
        for source, items in grouped.items():
            lines += [f"## {self._escape(source)}", ""]
            for article in items:
                lines += [
                    self._article_heading(article),
                    article["summary"] or article["digest"] or "等待整理",
                    "",
                ]
        lines += ["---", f"生成时间：{self.storage.local_now().strftime('%Y-%m-%d %H:%M:%S')}"]
        markdown = "\n".join(lines)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{day.isoformat()}.md"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.dir, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(markdown)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.storage.save_briefing(day, overview, markdown, str(path), len(articles))
        return {
            "day": day.isoformat(),
            "overview": overview,
            "markdown": markdown,
            "file_path": str(path),
            "article_count": len(articles),
            "themes": themes,
        }

    @staticmethod
    def _escape(value: str) -> str:
        return " ".join(value.split()).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")

    @classmethod
    def _article_heading(cls, article: dict) -> str:
        url = article["url"].replace("<", "%3C").replace(">", "%3E").replace("\n", "").replace("\r", "")
        return f"### [{cls._escape(article['title'])}](<{url}>)"
