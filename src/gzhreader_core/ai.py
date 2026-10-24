from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from .credentials import CredentialVault

TIMEOUT_SECONDS = 90
RETRIES = 2
TEMPERATURE = 0.2
PROMPT_VERSION = "article-summary-v1"


class Summarizer:
    def __init__(self, vault: CredentialVault, client_factory=httpx.Client):
        self.vault = vault
        self.client_factory = client_factory

    def config(self) -> dict[str, Any]:
        return self.vault.load("ai")

    def save(self, payload: dict[str, Any]) -> None:
        self.vault.save("ai", self._merge_config(payload))

    def _merge_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("AI 设置格式无效")
        current = self.config()
        result = {}
        for key in ("base_url", "model", "api_key"):
            value = payload.get(key, current.get(key, ""))
            if key == "api_key" and value is None:
                value = current.get(key, "")
            if not isinstance(value, str):
                raise ValueError("AI 服务地址、密钥和模型名称必须为文本")
            result[key] = value.strip()
        enabled = payload.get("enabled", current.get("enabled", True))
        if not isinstance(enabled, bool):
            raise ValueError("AI 开关必须为布尔值")
        result["enabled"] = enabled
        if result["base_url"]:
            self._endpoint(result["base_url"])
        return result

    def public_config(self) -> dict[str, Any]:
        config = self.config()
        return {
            "base_url": config.get("base_url", ""),
            "model": config.get("model", ""),
            "has_api_key": bool(config.get("api_key")),
            "enabled": bool(config.get("enabled", True)),
        }

    @staticmethod
    def _endpoint(base_url: str) -> str:
        value = base_url.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("请填写有效的 HTTP 或 HTTPS 服务地址")
        return value if parsed.path.endswith("/chat/completions") else value + "/chat/completions"

    def test(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            config = self._merge_config(payload)
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        if not config.get("base_url") or not config.get("api_key") or not config.get("model"):
            return {"ok": False, "message": "请完整填写服务地址、密钥和模型名称"}
        body = {
            "model": config["model"],
            "temperature": TEMPERATURE,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "只回复：连接成功"}],
        }
        try:
            with self.client_factory(timeout=20) as client:
                response = client.post(
                    self._endpoint(str(config["base_url"])),
                    headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
                    json=body,
                )
            if response.status_code in {401, 403}:
                return {"ok": False, "message": "密钥无效"}
            if response.status_code in {400, 404, 422}:
                text = response.text.lower()
                if "model" in text:
                    return {"ok": False, "message": "模型不可用"}
                return {"ok": False, "message": "服务地址无法访问"}
            if response.status_code >= 400:
                return {"ok": False, "message": "服务地址无法访问"}
            self._response_text(response)
            return {"ok": True, "message": "连接成功"}
        except httpx.TimeoutException:
            return {"ok": False, "message": "连接超时"}
        except Exception:
            return {"ok": False, "message": "服务地址无法访问"}

    @staticmethod
    def fallback(text: str) -> dict[str, Any]:
        summary = " ".join((text or "").split())[:180]
        return {
            "summary": summary,
            "key_points": [],
            "tags": [],
            "takeaway": summary[:60],
            "raw_text": "",
            "prompt_version": PROMPT_VERSION,
            "status": "fallback",
        }

    def summarize(self, title: str, content: str, source: str) -> dict[str, Any]:
        config = self.config()
        text = (content or title).strip()
        if not config.get("enabled", True) or not all(config.get(key) for key in ("api_key", "base_url", "model")):
            return self.fallback(text)
        prompt = (
            "请整理下面的微信公众号文章，只返回 JSON，不要 Markdown。"
            "字段必须为 summary（120到250个中文字符）、key_points（3到5条）、"
            "tags（1到3个）、takeaway（一句话结论）。\n"
            f"公众号：{source}\n标题：{title}\n正文：{text[:12000]}"
        )
        body = {
            "model": config["model"],
            "temperature": TEMPERATURE,
            "messages": [
                {"role": "system", "content": "你是克制、准确的中文内容编辑。不要补充文章没有的信息。"},
                {"role": "user", "content": prompt},
            ],
        }
        last_raw = ""
        for _attempt in range(RETRIES + 1):
            try:
                with self.client_factory(timeout=TIMEOUT_SECONDS) as client:
                    response = client.post(
                        self._endpoint(str(config["base_url"])),
                        headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
                        json=body,
                    )
                response.raise_for_status()
                last_raw = self._response_text(response)
                data = self._parse(last_raw)
                return {
                    **data,
                    "raw_text": last_raw,
                    "prompt_version": PROMPT_VERSION,
                    "status": "done",
                }
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500 and exc.response.status_code not in {408, 429}:
                    break
            except Exception:
                continue
        result = self.fallback(text)
        result["raw_text"] = last_raw
        return result

    @staticmethod
    def _response_text(response: httpx.Response) -> str:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("AI 响应格式无效")
        choices = value.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("AI 响应没有返回内容")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI 响应没有返回内容")
        return content.strip()

    @staticmethod
    def _parse(raw: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.S)
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        value = json.loads(match.group(0) if match else cleaned)
        if not isinstance(value, dict):
            raise ValueError("摘要格式无效")
        summary = value.get("summary")
        takeaway = value.get("takeaway", "")
        if not isinstance(summary, str) or not summary.strip() or not isinstance(takeaway, str):
            raise ValueError("摘要格式无效")
        result = {"summary": summary.strip(), "takeaway": takeaway.strip()}
        for key, limit in (("key_points", 5), ("tags", 3)):
            items = value.get(key, [])
            if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
                raise ValueError("摘要格式无效")
            result[key] = [item.strip() for item in items if item.strip()][:limit]
        return result
