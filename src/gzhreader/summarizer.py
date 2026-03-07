from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from .config import LLMConfig


@dataclass(slots=True)
class SummarizeInput:
    title: str
    content: str
    author: str


class OpenAICompatibleSummarizer:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.api_key = config.api_key or os.getenv("OPENAI_API_KEY", "")

    def summarize(self, item: SummarizeInput) -> str:
        text = (item.content or "").strip() or item.title
        if not self.api_key:
            return self._fallback_summary(text)
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [
                {
                    "role": "system",
                    "content": "你是中文科技资讯编辑，请输出 2 到 4 句简洁摘要，保留关键事实、结论和影响，避免空话。",
                },
                {
                    "role": "user",
                    "content": f"标题：{item.title}\n作者：{item.author}\n正文：\n{text[:5000]}",
                },
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for _ in range(self.config.retries + 1):
            try:
                with httpx.Client(timeout=self.config.timeout_seconds) as client:
                    response = client.post(f"{self.config.base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if content:
                        return content
            except Exception:
                continue
        return self._fallback_summary(text)

    def check_connectivity(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "缺少 llm.api_key 或 OPENAI_API_KEY"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        timeout = min(self.config.timeout_seconds, 15)
        try:
            with httpx.Client(timeout=timeout) as client:
                models_response = client.get(
                    f"{self.config.base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if models_response.status_code < 400:
                    return True, "/models 可用"
                if models_response.status_code not in {404, 405}:
                    return False, f"模型接口返回 HTTP {models_response.status_code}"

                payload = {
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                }
                chat_response = client.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if chat_response.status_code >= 400:
                    return False, f"chat/completions 返回 HTTP {chat_response.status_code}，/models 也不可用"
                return True, "chat/completions 可用，/models 不可用也没关系"
        except Exception as exc:
            return False, f"连通性检查失败: {exc}"

    def _fallback_summary(self, text: str) -> str:
        compact = " ".join(text.split())
        if len(compact) <= 180:
            return compact
        return f"{compact[:180]}..."
