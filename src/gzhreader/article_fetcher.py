from __future__ import annotations

import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from readability import Document

from .config import ArticleFetchConfig, RSSConfig
from .types import FeedArticle

logger = logging.getLogger(__name__)

MIN_RSS_FULLTEXT_CHARS = 400
MIN_FETCHED_CONTENT_CHARS = 200
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class BrowserCandidate:
    channel: str
    executable_path: str | None


@dataclass(slots=True)
class FetchedArticleContent:
    title: str
    author: str
    publish_time: datetime | None
    content_text: str
    raw_html: str
    content_source: str
    capture_status: str
    fetch_error: str = ""

    @property
    def success(self) -> bool:
        return bool(self.content_text.strip()) and self.content_source in {"http_fulltext", "browser_dom"}


class ArticleContentFetcher:
    def __init__(self, config: ArticleFetchConfig, rss_config: RSSConfig) -> None:
        self.config = config
        self.rss_config = rss_config

    def check_http_runtime(self) -> tuple[bool, str]:
        try:
            import httpx as _httpx  # noqa: F401
            import bs4 as _bs4  # noqa: F401
            import readability as _readability  # noqa: F401
        except Exception as exc:
            return False, f"依赖不可用: {exc}"
        return True, "httpx + BeautifulSoup + readability 可用"

    def check_browser_runtime(self) -> tuple[bool, str]:
        if not self.config.enabled:
            return True, "article_fetch 已禁用"
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except Exception as exc:
            return False, f"缺少 playwright: {exc}"
        candidate = self._find_first_browser_candidate()
        if candidate is None:
            return False, "未找到本机 Edge/Chrome 可执行文件"
        label = candidate.channel
        detail = candidate.executable_path or candidate.channel
        return True, f"可用浏览器: {label} ({detail})"

    def should_fetch(self, item: FeedArticle) -> bool:
        if not self.config.enabled:
            return False
        if self.config.trigger != "missing_rss_content":
            return False
        if not item.url:
            return False
        return len(self._normalize_text(item.content_text)) < MIN_RSS_FULLTEXT_CHARS

    def fetch(
        self,
        url: str,
        *,
        fallback_title: str = "",
        fallback_author: str = "",
        fallback_publish_time: datetime | None = None,
    ) -> FetchedArticleContent:
        http_result = self._fetch_via_http(
            url,
            fallback_title=fallback_title,
            fallback_author=fallback_author,
            fallback_publish_time=fallback_publish_time,
        )
        if http_result.success:
            return http_result

        browser_result = self._fetch_via_browser(
            url,
            fallback_title=fallback_title,
            fallback_author=fallback_author,
            fallback_publish_time=fallback_publish_time,
        )
        if browser_result.success:
            return browser_result

        error_parts = [part for part in [http_result.fetch_error, browser_result.fetch_error] if part]
        return FetchedArticleContent(
            title=fallback_title,
            author=fallback_author,
            publish_time=fallback_publish_time,
            content_text="",
            raw_html="",
            content_source="fetch_failed",
            capture_status="fetch_failed",
            fetch_error="; ".join(error_parts) or "fetch failed",
        )

    def _fetch_via_http(
        self,
        url: str,
        *,
        fallback_title: str,
        fallback_author: str,
        fallback_publish_time: datetime | None,
    ) -> FetchedArticleContent:
        logger.info("Content fetch: HTTP request %s", url)
        headers = {
            "User-Agent": DESKTOP_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        try:
            started = time.perf_counter()
            with httpx.Client(timeout=self.config.timeout_seconds, follow_redirects=True, headers=headers) as client:
                response = client.get(url)
                response.raise_for_status()
            elapsed = time.perf_counter() - started
            logger.info("Content fetch: HTTP ok in %.2fs for %s", elapsed, url)
            return self._extract_from_html(
                response.text,
                url,
                content_source="http_fulltext",
                fallback_title=fallback_title,
                fallback_author=fallback_author,
                fallback_publish_time=fallback_publish_time,
            )
        except Exception as exc:
            logger.warning("Content fetch: HTTP failed for %s: %s", url, exc)
            return FetchedArticleContent(
                title=fallback_title,
                author=fallback_author,
                publish_time=fallback_publish_time,
                content_text="",
                raw_html="",
                content_source="fetch_failed",
                capture_status="fetch_failed",
                fetch_error=f"http failed: {exc}",
            )

    def _fetch_via_browser(
        self,
        url: str,
        *,
        fallback_title: str,
        fallback_author: str,
        fallback_publish_time: datetime | None,
    ) -> FetchedArticleContent:
        logger.info("Content fetch: browser fallback start %s", url)
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            logger.warning("Content fetch: playwright unavailable: %s", exc)
            return FetchedArticleContent(
                title=fallback_title,
                author=fallback_author,
                publish_time=fallback_publish_time,
                content_text="",
                raw_html="",
                content_source="fetch_failed",
                capture_status="fetch_failed",
                fetch_error=f"browser unavailable: {exc}",
            )

        candidates = list(self._iter_browser_candidates())
        if not candidates:
            return FetchedArticleContent(
                title=fallback_title,
                author=fallback_author,
                publish_time=fallback_publish_time,
                content_text="",
                raw_html="",
                content_source="fetch_failed",
                capture_status="fetch_failed",
                fetch_error="no local browser found",
            )

        errors: list[str] = []
        for candidate in candidates:
            try:
                with sync_playwright() as playwright:
                    browser_type = playwright.chromium
                    with tempfile.TemporaryDirectory(prefix="gzhreader-pw-") as user_data_dir:
                        launch_kwargs = {
                            "user_data_dir": user_data_dir,
                            "headless": True,
                            "args": ["--disable-extensions", "--disable-sync", "--no-first-run"],
                        }
                        if candidate.executable_path:
                            launch_kwargs["executable_path"] = candidate.executable_path
                        else:
                            launch_kwargs["channel"] = candidate.channel
                        started = time.perf_counter()
                        context = browser_type.launch_persistent_context(**launch_kwargs)
                        try:
                            page = context.pages[0] if context.pages else context.new_page()
                            page.goto(url, wait_until="domcontentloaded", timeout=self.config.timeout_seconds * 1000)
                            try:
                                page.wait_for_load_state("networkidle", timeout=min(self.config.timeout_seconds * 1000, 5000))
                            except Exception:
                                pass
                            page.wait_for_timeout(1200)
                            html = page.content()
                        finally:
                            context.close()
                    elapsed = time.perf_counter() - started
                    logger.info(
                        "Content fetch: browser ok in %.2fs via %s for %s",
                        elapsed,
                        candidate.channel,
                        url,
                    )
                    result = self._extract_from_html(
                        html,
                        url,
                        content_source="browser_dom",
                        fallback_title=fallback_title,
                        fallback_author=fallback_author,
                        fallback_publish_time=fallback_publish_time,
                    )
                    if result.success:
                        return result
                    errors.append(f"{candidate.channel}: extracted content too short")
            except PlaywrightError as exc:
                logger.warning("Content fetch: browser %s failed for %s: %s", candidate.channel, url, exc)
                errors.append(f"{candidate.channel}: {exc}")
            except Exception as exc:
                logger.warning("Content fetch: browser %s unexpected failure for %s: %s", candidate.channel, url, exc)
                errors.append(f"{candidate.channel}: {exc}")

        return FetchedArticleContent(
            title=fallback_title,
            author=fallback_author,
            publish_time=fallback_publish_time,
            content_text="",
            raw_html="",
            content_source="fetch_failed",
            capture_status="fetch_failed",
            fetch_error="browser failed: " + "; ".join(errors),
        )

    def _extract_from_html(
        self,
        html: str,
        url: str,
        *,
        content_source: str,
        fallback_title: str,
        fallback_author: str,
        fallback_publish_time: datetime | None,
    ) -> FetchedArticleContent:
        if "mp.weixin.qq.com" in url:
            extracted = self._extract_wechat_html(
                html,
                fallback_title=fallback_title,
                fallback_author=fallback_author,
                fallback_publish_time=fallback_publish_time,
            )
            if extracted is not None:
                title, author, publish_time, content_text = extracted
                return FetchedArticleContent(
                    title=title,
                    author=author,
                    publish_time=publish_time,
                    content_text=content_text,
                    raw_html=html,
                    content_source=content_source,
                    capture_status=content_source,
                )

        generic = self._extract_generic_html(
            html,
            fallback_title=fallback_title,
            fallback_author=fallback_author,
            fallback_publish_time=fallback_publish_time,
        )
        if generic is None:
            return FetchedArticleContent(
                title=fallback_title,
                author=fallback_author,
                publish_time=fallback_publish_time,
                content_text="",
                raw_html=html,
                content_source="fetch_failed",
                capture_status="fetch_failed",
                fetch_error="content too short after extraction",
            )

        title, author, publish_time, content_text = generic
        return FetchedArticleContent(
            title=title,
            author=author,
            publish_time=publish_time,
            content_text=content_text,
            raw_html=html,
            content_source=content_source,
            capture_status=content_source,
        )

    def _extract_wechat_html(
        self,
        html: str,
        *,
        fallback_title: str,
        fallback_author: str,
        fallback_publish_time: datetime | None,
    ) -> tuple[str, str, datetime | None, str] | None:
        soup = BeautifulSoup(html, "html.parser")
        content_node = soup.select_one("#js_content")
        if content_node is None:
            return None

        for selector in ["script", "style", ".qr_code_pc_outer", ".original_primary_card_tips", ".wx_profile_card_inner"]:
            for node in content_node.select(selector):
                node.decompose()

        title = self._first_non_empty(
            self._node_text(soup.select_one("#activity-name")),
            self._node_text(soup.select_one(".rich_media_title")),
            self._meta_content(soup, property_name="og:title"),
            fallback_title,
        )
        author = self._first_non_empty(
            self._node_text(soup.select_one("#js_name")),
            self._node_text(soup.select_one(".wx_follow_nickname")),
            self._meta_content(soup, name="author"),
            fallback_author,
        )
        publish_time = self._parse_datetime(
            self._first_non_empty(
                self._node_text(soup.select_one("#publish_time")),
                self._node_text(soup.select_one("#js_publish_time")),
                self._meta_content(soup, property_name="article:published_time"),
            )
        ) or fallback_publish_time
        content_text = self._clean_text(content_node.get_text("\n", strip=True))
        if len(content_text) < MIN_FETCHED_CONTENT_CHARS:
            return None
        return title, author, publish_time, self._truncate_content(content_text)

    def _extract_generic_html(
        self,
        html: str,
        *,
        fallback_title: str,
        fallback_author: str,
        fallback_publish_time: datetime | None,
    ) -> tuple[str, str, datetime | None, str] | None:
        soup = BeautifulSoup(html, "html.parser")
        title = self._first_non_empty(
            self._node_text(soup.title),
            self._meta_content(soup, property_name="og:title"),
            fallback_title,
        )
        author = self._first_non_empty(
            self._meta_content(soup, name="author"),
            fallback_author,
        )
        publish_time = self._parse_datetime(
            self._first_non_empty(
                self._meta_content(soup, property_name="article:published_time"),
                self._meta_content(soup, name="pubdate"),
                self._meta_content(soup, name="publishdate"),
            )
        ) or fallback_publish_time

        article_html = ""
        try:
            article_html = Document(html).summary(html_partial=True)
        except Exception:
            article_html = ""

        candidates: list[str] = []
        if article_html:
            candidates.append(article_html)
        if soup.body is not None:
            candidates.append(str(soup.body))
        candidates.append(html)

        for candidate_html in candidates:
            text = self._clean_text(BeautifulSoup(candidate_html, "html.parser").get_text("\n", strip=True))
            if len(text) >= MIN_FETCHED_CONTENT_CHARS:
                return title, author, publish_time, self._truncate_content(text)
        return None

    def _iter_browser_candidates(self):
        seen: set[str] = set()
        for channel in self.config.browser_channel_order:
            executable = self._find_browser_executable(channel)
            key = f"{channel}|{executable or ''}"
            if key in seen:
                continue
            seen.add(key)
            if executable or channel in {"chrome", "msedge"}:
                yield BrowserCandidate(channel=channel, executable_path=executable)

    def _find_first_browser_candidate(self) -> BrowserCandidate | None:
        return next(iter(self._iter_browser_candidates()), None)

    def _find_browser_executable(self, channel: str) -> str | None:
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", "")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "")

        candidates: dict[str, list[str]] = {
            "chrome": [
                os.path.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
            ],
            "msedge": [
                os.path.join(local_app_data, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(program_files, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(program_files_x86, "Microsoft", "Edge", "Application", "msedge.exe"),
            ],
        }

        for candidate in candidates.get(channel, []):
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _meta_content(self, soup: BeautifulSoup, *, name: str | None = None, property_name: str | None = None) -> str:
        if property_name:
            node = soup.find("meta", attrs={"property": property_name})
            if node and node.get("content"):
                return str(node.get("content")).strip()
        if name:
            node = soup.find("meta", attrs={"name": name})
            if node and node.get("content"):
                return str(node.get("content")).strip()
        return ""

    def _node_text(self, node) -> str:
        if node is None:
            return ""
        return self._normalize_text(node.get_text(" ", strip=True))

    def _clean_text(self, text: str) -> str:
        normalized = text.replace("\r", "\n").replace("\xa0", " ")
        lines: list[str] = []
        for raw_line in normalized.split("\n"):
            line = self._normalize_text(raw_line)
            if not line:
                continue
            if lines and lines[-1] == line:
                continue
            lines.append(line)
        return "\n\n".join(lines)

    def _normalize_text(self, text: str) -> str:
        return " ".join((text or "").split())

    def _truncate_content(self, text: str) -> str:
        stripped = text.strip()
        if len(stripped) <= self.config.max_content_chars:
            return stripped
        return stripped[: self.config.max_content_chars].rstrip()

    def _first_non_empty(self, *values: str | None) -> str:
        for value in values:
            if value and str(value).strip():
                return str(value).strip()
        return ""

    def _parse_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        normalized = value.strip().replace("Z", "+00:00")
        for candidate in [normalized, normalized.replace("/", "-")]:
            try:
                return datetime.fromisoformat(candidate)
            except ValueError:
                continue
        for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue
        return None
