from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class FeedConfig(StrictBaseModel):
    name: str
    url: str = ""
    active: bool = True
    order: int = 0
    author: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("feed name must not be empty")
        return value


class ScheduleConfig(StrictBaseModel):
    daily_time: str = "21:30"
    timezone: str = "Asia/Shanghai"

    @field_validator("daily_time")
    @classmethod
    def _validate_time(cls, value: str) -> str:
        _validate_hhmm(value)
        return value


class RSSConfig(StrictBaseModel):
    timezone: str = "Asia/Shanghai"
    day_start: str = "00:00"
    request_timeout_seconds: int = 20
    user_agent: str = "GZHReader/0.2"
    max_articles_per_feed: int = 20

    @field_validator("day_start")
    @classmethod
    def _validate_day_start(cls, value: str) -> str:
        _validate_hhmm(value)
        return value

    @model_validator(mode="after")
    def _validate_model(self) -> "RSSConfig":
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.max_articles_per_feed <= 0:
            raise ValueError("max_articles_per_feed must be positive")
        return self


class WeWeRSSConfig(StrictBaseModel):
    enabled: bool = True
    base_url: str = "http://localhost:4000"
    auth_code: str = "123567"
    service_dir: str = "./infra/wewe-rss"
    compose_variant: Literal["sqlite", "mysql"] = "sqlite"
    port: int = 4000
    server_origin_url: str = "http://localhost:4000"
    image: str = "cooderl/wewe-rss:latest"

    @model_validator(mode="after")
    def _validate_model(self) -> "WeWeRSSConfig":
        if self.port <= 0:
            raise ValueError("port must be positive")
        return self


class ArticleFetchConfig(StrictBaseModel):
    enabled: bool = True
    trigger: Literal["missing_rss_content"] = "missing_rss_content"
    mode: Literal["hybrid"] = "hybrid"
    timeout_seconds: int = 20
    browser_channel_order: list[str] = Field(default_factory=lambda: ["msedge", "chrome"])
    max_content_chars: int = 12000

    @model_validator(mode="after")
    def _validate_model(self) -> "ArticleFetchConfig":
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_content_chars <= 0:
            raise ValueError("max_content_chars must be positive")
        if not self.browser_channel_order:
            raise ValueError("browser_channel_order must not be empty")
        return self


class LLMConfig(StrictBaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 45
    retries: int = 2
    temperature: float = 0.2

    @model_validator(mode="after")
    def _validate_model(self) -> "LLMConfig":
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.retries < 0:
            raise ValueError("retries must be non-negative")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        return self


class OutputConfig(StrictBaseModel):
    briefing_dir: str = "./output/briefings"
    raw_archive_dir: str = "./output/raw"
    log_level: str = "INFO"


class AppConfig(StrictBaseModel):
    db_path: str = "./data/gzhreader.db"
    feeds: list[FeedConfig] = Field(default_factory=list)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    rss: RSSConfig = Field(default_factory=RSSConfig)
    wewe_rss: WeWeRSSConfig = Field(default_factory=WeWeRSSConfig)
    article_fetch: ArticleFetchConfig = Field(default_factory=ArticleFetchConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_shape(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        migrated = dict(data)

        if "feeds" not in migrated and isinstance(migrated.get("accounts"), list):
            feeds: list[dict[str, Any]] = []
            for item in migrated["accounts"]:
                if not isinstance(item, dict):
                    continue
                feeds.append(
                    {
                        "name": item.get("name") or item.get("wechat_id") or "Unnamed Feed",
                        "url": item.get("url", ""),
                        "active": item.get("active", True),
                        "order": item.get("order", 0),
                        "author": item.get("name") or item.get("wechat_id"),
                    }
                )
            migrated["feeds"] = feeds

        output = migrated.get("output")
        if isinstance(output, dict) and "raw_archive_dir" not in output and "html_archive_dir" in output:
            updated_output = dict(output)
            updated_output["raw_archive_dir"] = updated_output.get("html_archive_dir")
            migrated["output"] = updated_output

        return migrated


def default_config() -> AppConfig:
    return AppConfig(
        feeds=[
            FeedConfig(
                name="?????",
                url="http://localhost:4000/feeds/replace-me.atom",
                active=True,
                order=1,
            )
        ]
    )


def ensure_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"???????: {path}")
    return load_config(path)


def load_config(path: Path) -> AppConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def save_config(config: AppConfig, path: Path) -> None:
    payload = config.model_dump(mode="json")
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")


def dump_json(config: AppConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _validate_hhmm(value: str) -> None:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("time must use HH:MM format")
    hour, minute = parts
    if not hour.isdigit() or not minute.isdigit():
        raise ValueError("time must use HH:MM format")
    hour_int = int(hour)
    minute_int = int(minute)
    if not (0 <= hour_int <= 23 and 0 <= minute_int <= 59):
        raise ValueError("time must be a valid 24-hour clock value")
