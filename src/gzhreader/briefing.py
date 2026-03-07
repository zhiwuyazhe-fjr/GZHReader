from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from .types import ArticleView


class BriefingBuilder:
    def build(self, target_date: date, views: list[ArticleView]) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"# 公众号日报 - {target_date.isoformat()}", "", f"- 生成时间：{now}", f"- 文章总数：{len(views)}", ""]
        grouped: dict[str, list[ArticleView]] = defaultdict(list)
        for view in views:
            grouped[view.feed_name].append(view)

        if not grouped:
            lines.append("> 当天没有匹配到新文章。")
            lines.append("")
            return "\n".join(lines).rstrip() + "\n"

        for feed_name in sorted(grouped):
            items = grouped[feed_name]
            lines.append(f"## {feed_name}")
            lines.append("")
            succeeded = [item for item in items if item.summary_status == "done"]
            failed = [item for item in items if item.summary_status == "failed"]
            if not succeeded:
                lines.append("> 当天没有成功总结的文章。")
                lines.append("")
            for item in succeeded:
                lines.append(f"### {item.title}")
                lines.append(f"- 发布时间：{item.publish_time.strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"- 作者：{item.author or '未知'}")
                lines.append(f"- 内容来源：{item.content_source}")
                lines.append(f"- 摘要：{item.summary}")
                if item.url:
                    lines.append(f"- 原文链接：{item.url}")
                lines.append("")
            if failed:
                lines.append("### 失败项")
                for item in failed:
                    lines.append(f"- {item.title or '未命名文章'}：{item.summary_error or '摘要失败'}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"
