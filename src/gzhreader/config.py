from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import __version__
from .runtime_paths import get_runtime_paths, is_frozen_app


def build_default_source_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/") or "http://127.0.0.1:4000"
    return f"{normalized}/feeds/all.atom"


DAILY_ARTICLE_LIMIT_PRESETS: tuple[Literal["all"] | int, ...] = ("all", 20, 30, 40, 50, 100)
LEGACY_DB_PATHS = ("./data/gzhreader.db", "data/gzhreader.db")
LEGACY_BRIEFING_DIRS = ("./output/briefings", "output/briefings")
LEGACY_RAW_ARCHIVE_DIRS = ("./output/raw", "output/raw")
LEGACY_RSS_SERVICE_DATA_DIRS = (
    "./infra/wewe-rss/data",
    "infra/wewe-rss/data",
    "./.runtime/wewe-rss/data",
    ".runtime/wewe-rss/data",
)
LEGACY_RSS_SERVICE_LOG_FILES = ("./logs/wewe-rss.log", "logs/wewe-rss.log")
LEGACY_RSS_SERVICE_AUTH_CODES = ("123567",)
DEFAULT_RSS_USER_AGENT = f"GZHReader/{__version__}"


def normalize_daily_article_limit(value: Any) -> Literal["all"] | int:
    if isinstance(value, bool):
        raise ValueError("daily_article_limit must be 'all' or a positive integer")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "all":
            return "all"
        if normalized.isdigit():
            value = int(normalized)
        else:
            raise ValueError("daily_article_limit must be 'all' or a positive integer")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("daily_article_limit must be positive")
        return value
    raise ValueError("daily_article_limit must be 'all' or a positive integer")


def describe_daily_article_limit(value: Literal["all"] | int) -> str:
    normalized = normalize_daily_article_limit(value)
    if normalized == "all":
        return "当天全部"
    return f"每天最多 {normalized} 篇"


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


class SourceConfig(StrictBaseModel):
    mode: Literal["aggregate"] = "aggregate"
    url: str = "http://127.0.0.1:4000/feeds/all.atom"

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source url must not be empty")
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
    user_agent: str = DEFAULT_RSS_USER_AGENT
    daily_article_limit: Literal["all"] | int = 20

    @field_validator("day_start")
    @classmethod
    def _validate_day_start(cls, value: str) -> str:
        _validate_hhmm(value)
        return value

    @field_validator("daily_article_limit", mode="before")
    @classmethod
    def _validate_daily_article_limit(cls, value: Any) -> Literal["all"] | int:
        return normalize_daily_article_limit(value)

    @model_validator(mode="after")
    def _validate_model(self) -> "RSSConfig":
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        return self


class RSSServiceConfig(StrictBaseModel):
    mode: Literal["bundled_wewe_rss"] = "bundled_wewe_rss"
    base_url: str = "http://127.0.0.1:4000"
    auth_code: str = ""
    port: int = 4000
    host: str = "127.0.0.1"
    data_dir: str = "./.runtime/wewe-rss/data"
    log_file: str = "./logs/wewe-rss.log"

    @field_validator("host")
    @classmethod
    def _validate_host(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("host must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_model(self) -> "RSSServiceConfig":
        if self.port <= 0:
            raise ValueError("port must be positive")
        self.auth_code = ""
        if not self.base_url.strip():
            self.base_url = f"http://{self.host}:{self.port}"
        elif self.base_url.strip().lower().startswith("http://localhost:"):
            self.base_url = f"http://127.0.0.1:{self.port}"
        if not self.data_dir.strip():
            raise ValueError("data_dir must not be empty")
        if not self.log_file.strip():
            raise ValueError("log_file must not be empty")
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
    save_raw_html: bool = False
    log_level: str = "INFO"


class AppConfig(StrictBaseModel):
    db_path: str = "./data/gzhreader.db"
    source: SourceConfig = Field(default_factory=SourceConfig)
    feeds: list[FeedConfig] = Field(default_factory=list)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    rss: RSSConfig = Field(default_factory=RSSConfig)
    rss_service: RSSServiceConfig = Field(default_factory=RSSServiceConfig)
    article_fetch: ArticleFetchConfig = Field(default_factory=ArticleFetchConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_shape(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        migrated = dict(data)
        legacy_feeds: list[dict[str, Any]] = []

        if isinstance(migrated.get("feeds"), list):
            for item in migrated["feeds"]:
                if isinstance(item, dict):
                    legacy_feeds.append(dict(item))

        if isinstance(migrated.get("accounts"), list):
            for item in migrated["accounts"]:
                if not isinstance(item, dict):
                    continue
                legacy_feeds.append(
                    {
                        "name": item.get("name") or item.get("wechat_id") or "全部公众号",
                        "url": item.get("url", ""),
                        "active": item.get("active", True),
                        "order": item.get("order", 0),
                        "author": item.get("name") or item.get("wechat_id"),
                    }
                )

        legacy_wewe_rss = migrated.get("wewe_rss") if isinstance(migrated.get("wewe_rss"), dict) else {}
        if "rss_service" not in migrated:
            base_url = str(legacy_wewe_rss.get("base_url") or "http://127.0.0.1:4000").strip()
            legacy_service_dir = str(legacy_wewe_rss.get("service_dir") or "").strip()
            port = legacy_wewe_rss.get("port")
            if not port:
                try:
                    parsed = urlparse(base_url)
                    port = parsed.port or 4000
                except ValueError:
                    port = 4000
            migrated["rss_service"] = {
                "mode": "bundled_wewe_rss",
                "base_url": base_url or "http://127.0.0.1:4000",
                "auth_code": "",
                "port": int(port),
                "host": "127.0.0.1",
                "data_dir": (str(Path(legacy_service_dir) / "data") if legacy_service_dir else "./.runtime/wewe-rss/data"),
                "log_file": "./logs/wewe-rss.log",
            }

        if "source" not in migrated:
            source_url = _pick_first_active_feed_url(legacy_feeds)
            if not source_url:
                rss_service = migrated.get("rss_service") if isinstance(migrated.get("rss_service"), dict) else {}
                base_url = str(rss_service.get("base_url") or "http://127.0.0.1:4000")
                source_url = build_default_source_url(base_url)
            migrated["source"] = {"mode": "aggregate", "url": source_url}

        rss = migrated.get("rss")
        if isinstance(rss, dict) and "daily_article_limit" not in rss and "max_articles_per_feed" in rss:
            updated_rss = dict(rss)
            updated_rss["daily_article_limit"] = updated_rss.get("max_articles_per_feed")
            updated_rss.pop("max_articles_per_feed", None)
            migrated["rss"] = updated_rss

        output = migrated.get("output")
        if isinstance(output, dict) and "raw_archive_dir" not in output and "html_archive_dir" in output:
            updated_output = dict(output)
            updated_output["raw_archive_dir"] = updated_output.get("html_archive_dir")
            migrated["output"] = updated_output

        return migrated

    @model_validator(mode="after")
    def _normalize_source(self) -> "AppConfig":
        if not self.source.url:
            self.source.url = build_default_source_url(self.rss_service.base_url)
        return self

    def runtime_feed(self) -> FeedConfig:
        return FeedConfig(
            name="全部公众号",
            url=self.source.url,
            active=True,
            order=1,
        )

    def runtime_feeds(self) -> list[FeedConfig]:
        if not self.source.url:
            return []
        return [self.runtime_feed()]


def default_config() -> AppConfig:
    runtime_paths = get_runtime_paths()
    return AppConfig(
        db_path=str(runtime_paths.db_path),
        source=SourceConfig(url=build_default_source_url("http://127.0.0.1:4000")),
        rss_service=RSSServiceConfig(
            base_url="http://127.0.0.1:4000",
            auth_code="",
            port=4000,
            host="127.0.0.1",
            data_dir=str(runtime_paths.rss_service_data_dir),
            log_file=str(runtime_paths.rss_service_log_path),
        ),
        output=OutputConfig(
            briefing_dir=str(runtime_paths.output_dir),
            raw_archive_dir=str(runtime_paths.raw_archive_dir),
        ),
    )


def ensure_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    config, _, _ = migrate_config_file(path)
    return config


def load_config(path: Path) -> AppConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def migrate_config_file(path: Path) -> tuple[AppConfig, bool, Path | None]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if _needs_legacy_reset(data):
        config = _reset_legacy_config(data)
        _apply_runtime_path_migration(config)
        backup_path = Path(f"{path}.legacy-reset.bak")
        if backup_path.exists():
            backup_path.unlink()
        shutil.move(path, backup_path)
        save_config(config, path)
        return config, True, backup_path

    config = AppConfig.model_validate(data)
    runtime_changed = _apply_runtime_path_migration(config)

    if not _needs_file_migration(data) and not runtime_changed:
        return config, False, None

    backup_path = Path(f"{path}.bak")
    shutil.copyfile(path, backup_path)
    save_config(config, path)
    return config, True, backup_path


def save_config(config: AppConfig, path: Path) -> None:
    payload = config.model_dump(mode="json", exclude={"feeds"})
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")


def dump_json(config: AppConfig) -> str:
    return json.dumps(config.model_dump(mode="json", exclude={"feeds"}), ensure_ascii=False, indent=2)


def _needs_file_migration(data: Any) -> bool:
    if not isinstance(data, dict):
        return True
    return any(
        key in data
        for key in (
            "accounts",
            "feeds",
            "wewe_rss",
        )
    ) or "source" not in data or "rss_service" not in data or _output_needs_migration(data) or _rss_needs_migration(data) or _rss_service_needs_migration(data)


def _needs_legacy_reset(data: Any) -> bool:
    if not isinstance(data, dict):
        return False

    if isinstance(data.get("wewe_rss"), dict):
        return True

    source = data.get("source")
    if isinstance(source, dict):
        source_url = str(source.get("url") or "").strip().lower()
        if source_url.startswith("http://localhost:"):
            return True

    rss_service = data.get("rss_service")
    if isinstance(rss_service, dict):
        auth_code = str(rss_service.get("auth_code") or "").strip()
        base_url = str(rss_service.get("base_url") or "").strip().lower()
        data_dir = str(rss_service.get("data_dir") or "").replace("\\", "/").strip().lower()
        if auth_code:
            return True
        if base_url.startswith("http://localhost:"):
            return True
        if "infra/wewe-rss" in data_dir:
            return True

    return False


def _reset_legacy_config(data: Any) -> AppConfig:
    config = default_config()
    if not isinstance(data, dict):
        return config

    schedule = data.get("schedule")
    if isinstance(schedule, dict):
        config.schedule = ScheduleConfig.model_validate(schedule)

    rss = data.get("rss")
    if isinstance(rss, dict):
        updated_rss = dict(rss)
        if "daily_article_limit" not in updated_rss and "max_articles_per_feed" in updated_rss:
            updated_rss["daily_article_limit"] = updated_rss.get("max_articles_per_feed")
            updated_rss.pop("max_articles_per_feed", None)
        config.rss = RSSConfig.model_validate(updated_rss)

    article_fetch = data.get("article_fetch")
    if isinstance(article_fetch, dict):
        config.article_fetch = ArticleFetchConfig.model_validate(article_fetch)

    llm = data.get("llm")
    if isinstance(llm, dict):
        config.llm = LLMConfig.model_validate(llm)

    output = data.get("output")
    if isinstance(output, dict):
        updated_output = dict(output)
        if "raw_archive_dir" not in updated_output and "html_archive_dir" in updated_output:
            updated_output["raw_archive_dir"] = updated_output.get("html_archive_dir")
            updated_output.pop("html_archive_dir", None)
        config.output = OutputConfig.model_validate(updated_output)

    port = _resolve_legacy_rss_port(data)
    config.rss_service = RSSServiceConfig(
        mode="bundled_wewe_rss",
        base_url=f"http://127.0.0.1:{port}",
        auth_code="",
        port=port,
        host="127.0.0.1",
        data_dir=config.rss_service.data_dir,
        log_file=config.rss_service.log_file,
    )
    config.source = SourceConfig(
        mode="aggregate",
        url=build_default_source_url(config.rss_service.base_url),
    )
    return config


def _resolve_legacy_rss_port(data: dict[str, Any]) -> int:
    candidates: list[Any] = []
    rss_service = data.get("rss_service")
    if isinstance(rss_service, dict):
        candidates.append(rss_service.get("port"))
        candidates.append(rss_service.get("base_url"))

    legacy_wewe_rss = data.get("wewe_rss")
    if isinstance(legacy_wewe_rss, dict):
        candidates.append(legacy_wewe_rss.get("port"))
        candidates.append(legacy_wewe_rss.get("base_url"))

    source = data.get("source")
    if isinstance(source, dict):
        candidates.append(source.get("url"))

    for candidate in candidates:
        if isinstance(candidate, int) and candidate > 0:
            return candidate
        if isinstance(candidate, str):
            value = candidate.strip()
            if value.isdigit():
                port = int(value)
                if port > 0:
                    return port
            if value:
                try:
                    parsed = urlparse(value)
                except ValueError:
                    continue
                if parsed.port and parsed.port > 0:
                    return parsed.port
    return 4000


def _output_needs_migration(data: dict[str, Any]) -> bool:
    output = data.get("output")
    return isinstance(output, dict) and "html_archive_dir" in output and "raw_archive_dir" not in output


def _rss_needs_migration(data: dict[str, Any]) -> bool:
    rss = data.get("rss")
    return isinstance(rss, dict) and "max_articles_per_feed" in rss and "daily_article_limit" not in rss


def _rss_service_needs_migration(data: dict[str, Any]) -> bool:
    rss_service = data.get("rss_service")
    if not isinstance(rss_service, dict):
        return False
    auth_code = str(rss_service.get("auth_code") or "").strip()
    if auth_code and auth_code in LEGACY_RSS_SERVICE_AUTH_CODES:
        return True
    if auth_code:
        return True
    base_url = str(rss_service.get("base_url") or "").strip().lower()
    if base_url.startswith("http://localhost:"):
        return True
    return False


def _apply_runtime_path_migration(config: AppConfig) -> bool:
    if not is_frozen_app():
        return False

    runtime_paths = get_runtime_paths()
    changed = False

    if _needs_runtime_path_update(config.db_path, LEGACY_DB_PATHS):
        config.db_path = str(runtime_paths.db_path)
        changed = True

    if _needs_runtime_path_update(config.output.briefing_dir, LEGACY_BRIEFING_DIRS):
        config.output.briefing_dir = str(runtime_paths.output_dir)
        changed = True

    if _needs_runtime_path_update(config.output.raw_archive_dir, LEGACY_RAW_ARCHIVE_DIRS):
        config.output.raw_archive_dir = str(runtime_paths.raw_archive_dir)
        changed = True

    if _needs_runtime_path_update(config.rss_service.data_dir, LEGACY_RSS_SERVICE_DATA_DIRS):
        config.rss_service.data_dir = str(runtime_paths.rss_service_data_dir)
        changed = True

    if _needs_runtime_path_update(config.rss_service.log_file, LEGACY_RSS_SERVICE_LOG_FILES):
        config.rss_service.log_file = str(runtime_paths.rss_service_log_path)
        changed = True

    if config.rss_service.auth_code.strip():
        config.rss_service.auth_code = ""
        changed = True

    if config.rss_service.base_url.strip().lower().startswith("http://localhost:"):
        config.rss_service.base_url = f"http://127.0.0.1:{config.rss_service.port}"
        changed = True

    return changed


def _needs_runtime_path_update(value: str, legacy_values: tuple[str, ...]) -> bool:
    normalized = value.replace('\\', '/').strip()
    return normalized in legacy_values


def _pick_first_active_feed_url(feeds: list[dict[str, Any]]) -> str:
    for feed in feeds:
        url = str(feed.get("url") or "").strip()
        if url and bool(feed.get("active", True)):
            return url
    for feed in feeds:
        url = str(feed.get("url") or "").strip()
        if url:
            return url
    return ""


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
