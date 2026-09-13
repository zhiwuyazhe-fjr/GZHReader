from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from ..credentials import CredentialVault
from ..models import ArticleRecord, ProviderBatch, ProviderHealth, SourceProfile

BASE_URL = "https://weread.qq.com"
AUTH_CODES = {-2041, -2012}
RATE_LIMIT_CODES = {-2010}


class WeReadError(RuntimeError):
    def __init__(
        self,
        code: str | int,
        message: str,
        reconnect: bool = False,
        cooldown: bool = False,
        cooldown_minutes: int = 360,
        verification_required: bool = False,
    ):
        super().__init__(message)
        self.code = str(code)
        self.reconnect = reconnect
        self.cooldown = cooldown
        self.cooldown_minutes = max(int(cooldown_minutes), 1)
        self.verification_required = verification_required


def is_risk_control_message(message: str) -> bool:
    value = str(message or "").lower()
    return any(marker in value for marker in ("频繁", "稍后再试", "操作太快", "risk control"))


def build_mp_url(original_id: str) -> str:
    original_id = str(original_id or "").strip()
    if not original_id:
        return ""
    token = quote(original_id.replace("~", "_"), safe="_-")
    return f"https://mp.weixin.qq.com/s/{token}"


def encode_weread_id(value: str) -> str:
    """Encode a WeRead book id for reader-page URLs."""
    source = str(value or "").strip()
    if not source:
        raise ValueError("微信读书内容标识无效")
    digest = hashlib.md5(source.encode("utf-8")).hexdigest()
    if source.isdigit():
        kind = "3"
        chunks = [format(int(source[index : index + 9]), "x") for index in range(0, len(source), 9)]
    else:
        kind = "4"
        chunks = ["".join(format(byte, "x") for byte in source.encode("utf-8"))]
    encoded = digest[:3] + kind + "2" + digest[-2:]
    for index, chunk in enumerate(chunks):
        encoded += format(len(chunk), "02x") + chunk
        if index < len(chunks) - 1:
            encoded += "g"
    if len(encoded) < 20:
        encoded += digest[: 20 - len(encoded)]
    return encoded + hashlib.md5(encoded.encode("utf-8")).hexdigest()[:3]


def build_mp_reader_url(book_id: str) -> str:
    source = str(book_id or "").strip()
    if not source.startswith("MP_WXS_"):
        raise ValueError("公众号标识无效")
    return f"{BASE_URL}/web/mp/reader/{encode_weread_id(source)}"


def raise_response_error(payload: dict) -> None:
    code = payload.get("errCode", payload.get("errcode", 0))
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        code = 0
    if not code:
        return
    message = str(payload.get("errMsg") or payload.get("errmsg") or "内容暂时无法更新")
    if code == -2041:
        if is_risk_control_message(message):
            raise WeReadError(
                code,
                "微信读书操作过于频繁，请 24 小时后再重新连接",
                reconnect=True,
                cooldown=True,
                cooldown_minutes=24 * 60,
            )
        raise WeReadError(
            code,
            "访问授权已过期，请在浏览器中重新验证",
            reconnect=True,
            verification_required=True,
        )
    if code in AUTH_CODES:
        raise WeReadError(code, "登录状态已失效", reconnect=True)
    if code in RATE_LIMIT_CODES:
        raise WeReadError(
            code,
            "更新过于频繁，请稍后再试",
            cooldown=True,
            cooldown_minutes=6 * 60,
        )
    raise WeReadError(code, message)


def parse_mp_articles(payload: dict, source: SourceProfile) -> tuple[list[ArticleRecord], int]:
    if not isinstance(payload, dict):
        raise WeReadError("invalid_response", "内容列表格式无效")
    raise_response_error(payload)
    groups = payload.get("reviews", []) or []
    articles: list[ArticleRecord] = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        group_time = group.get("createTime", 0)
        for sub_review in group.get("subReviews", []) or []:
            if not isinstance(sub_review, dict):
                continue
            review = sub_review.get("review") or {}
            mp_info = review.get("mpInfo") or {}
            review_id = review.get("reviewId") or sub_review.get("reviewId")
            if not review_id:
                continue
            title = str(mp_info.get("title") or review.get("title") or "").strip()
            if not title:
                continue
            timestamp = mp_info.get("time") or review.get("createTime") or group_time or 0
            try:
                published = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                published = datetime.now(timezone.utc)
            original_id = str(mp_info.get("originalId") or "")
            url = build_mp_url(original_id) or str(mp_info.get("url") or review.get("url") or "")
            if not url:
                url = f"https://mp.weixin.qq.com/s/{quote(str(review_id), safe='_-')}"
            articles.append(
                ArticleRecord(
                    source_id=source.id,
                    origin_id=str(review_id),
                    title=title,
                    url=url,
                    published_at=published,
                    author=source.name,
                    cover=str(mp_info.get("pic_url") or mp_info.get("picUrl") or ""),
                    digest=str(mp_info.get("content") or review.get("content") or ""),
                )
            )
    return articles, len(groups) if isinstance(groups, list) else 0


def extract_mp_content(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    content = soup.select_one("#js_content") or soup.select_one(".rich_media_content")
    if content is None:
        raise WeReadError("invalid_content", "正文暂时无法获取")
    for element in content.select("script, style"):
        element.decompose()
    return "\n".join(line.strip() for line in content.get_text("\n", strip=True).splitlines() if line.strip())


class WeReadProvider:
    def __init__(
        self,
        vault: CredentialVault,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.vault = vault
        self.client_factory = client_factory
        self.sleep = sleep
        self._health: ProviderHealth | None = None
        self._prefetched_pages: dict[tuple[str, int], dict] = {}

    def credentials(self) -> dict:
        return self.vault.load("weread")

    def cache_articles_page(self, source_id: str, offset: int, payload: dict) -> None:
        if isinstance(payload, dict):
            self._prefetched_pages[(source_id, int(offset))] = payload

    def health(self) -> ProviderHealth:
        if self._health and self._health.state != "ready":
            return self._health
        credentials = self.credentials()
        if credentials.get("cookie") and (credentials.get("ticket") or credentials.get("auth_header_value")):
            return ProviderHealth("ready", "微信读书已连接")
        return ProviderHealth("disconnected", "需要连接微信读书", True)

    def mark_ready(self) -> None:
        self._health = ProviderHealth("ready", "微信读书已连接")

    def mark_error(self, error: WeReadError) -> None:
        if error.cooldown:
            self._health = ProviderHealth("cooldown", str(error), error.reconnect)
        elif error.verification_required:
            self._health = ProviderHealth("verification", str(error), True)
        elif error.reconnect:
            self._health = ProviderHealth("disconnected", "登录状态已失效", True)
        else:
            self._health = ProviderHealth("error", "内容暂时无法更新")

    @staticmethod
    def _headers(
        cookie: str,
        ticket: str = "",
        include_ticket: bool = False,
        *,
        auth_header_name: str = "x-wr-ticket",
        randstr: str = "",
        wpa: str = "",
        user_agent: str = "",
        referer: str = "",
    ) -> dict[str, str]:
        if not cookie:
            raise WeReadError("missing", "需要连接微信读书", reconnect=True)
        headers = {
            "Cookie": cookie,
            "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": referer or f"{BASE_URL}/",
        }
        if include_ticket:
            if not ticket:
                raise WeReadError("missing_ticket", "登录状态已失效", reconnect=True)
            header_name = auth_header_name.lower()
            if header_name not in {"x-wr-ticket", "x-wrpa-0"}:
                header_name = "x-wr-ticket"
            headers[header_name] = ticket
            if header_name == "x-wr-ticket":
                if randstr:
                    headers["x-wr-randstr"] = randstr
                if wpa:
                    headers["x-wrpa-0"] = wpa
        return headers

    def _credential_headers(self, include_ticket: bool = False) -> dict[str, str]:
        credentials = self.credentials()
        if include_ticket and credentials.get("authorization_consumed"):
            raise WeReadError(
                -2041,
                "访问授权已使用，请在浏览器中重新确认后再刷新",
                reconnect=True,
                verification_required=True,
            )
        ticket = str(credentials.get("auth_header_value") or credentials.get("ticket") or "")
        return self._headers(
            str(credentials.get("cookie") or ""),
            ticket,
            include_ticket,
            auth_header_name=str(credentials.get("auth_header_name") or "x-wr-ticket"),
            randstr=str(credentials.get("randstr") or ""),
            wpa=str(credentials.get("wpa") or ""),
            user_agent=str(credentials.get("user_agent") or ""),
            referer=str(credentials.get("referer") or ""),
        )

    def _get_articles_page(self, source_id: str, offset: int, headers: dict[str, str] | None = None) -> dict:
        prefetched = self._prefetched_pages.pop((source_id, int(offset)), None)
        if prefetched is not None:
            raise_response_error(prefetched)
            return prefetched
        try:
            with self.client_factory(timeout=30, follow_redirects=True) as client:
                response = client.get(
                    f"{BASE_URL}/web/mp/articles",
                    params={"bookId": source_id, "offset": offset},
                    headers=headers or self._credential_headers(True),
                )
        except httpx.TimeoutException as exc:
            raise WeReadError("network_timeout", "连接超时") from exc
        except httpx.HTTPError as exc:
            raise WeReadError("network_error", "网络连接失败") from exc
        if response.status_code in {401, 403}:
            raise WeReadError(response.status_code, "登录状态已失效", reconnect=True)
        if response.status_code == 429:
            raise WeReadError(429, "更新过于频繁，请稍后再试", cooldown=True, cooldown_minutes=6 * 60)
        if response.status_code != 200:
            raise WeReadError(response.status_code, "内容暂时无法更新")
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeReadError("invalid_json", "内容列表格式无效") from exc
        raise_response_error(payload)
        return payload

    def list_articles(
        self,
        source: SourceProfile,
        limit: int = 20,
        cutoff: datetime | None = None,
        previous_cursor: int = 0,
        max_pages: int = 20,
    ) -> ProviderBatch:
        if not source.id.startswith("MP_WXS_"):
            raise WeReadError("invalid_book_id", "公众号标识无效")
        result: list[ArticleRecord] = []
        newest_cursor = 0
        reached_cursor = previous_cursor == 0
        offset = 0
        pages_scanned = 0
        for page in range(max(max_pages, 1)):
            if page:
                self.sleep(1)
            payload = self._get_articles_page(source.id, offset)
            articles, group_count = parse_mp_articles(payload, source)
            pages_scanned += 1
            if not group_count:
                reached_cursor = True
                break
            for article in articles:
                timestamp = int(article.published_at.timestamp())
                newest_cursor = max(newest_cursor, timestamp)
                if previous_cursor and timestamp <= previous_cursor:
                    reached_cursor = True
                    continue
                if cutoff and article.published_at < cutoff:
                    reached_cursor = True
                    continue
                result.append(article)
                if len(result) >= limit:
                    reached_cursor = True if previous_cursor == 0 else reached_cursor
                    break
            if len(result) >= limit or reached_cursor:
                break
            offset += group_count
        if previous_cursor and not reached_cursor:
            raise WeReadError("backlog_incomplete", "历史内容较多，本轮未能完成追赶")
        self.mark_ready()
        return ProviderBatch(result, newest_cursor, reached_cursor, pages_scanned)

    def fetch_content(self, review_id: str) -> str:
        headers = self._credential_headers(False)
        headers["Accept"] = "text/html,application/xhtml+xml,*/*"
        try:
            with self.client_factory(timeout=30, follow_redirects=True) as client:
                response = client.get(
                    f"{BASE_URL}/web/mp/content",
                    params={"reviewId": review_id},
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise WeReadError("network_timeout", "正文获取超时") from exc
        except httpx.HTTPError as exc:
            raise WeReadError("network_error", "正文暂时无法获取") from exc
        if response.status_code in {401, 403}:
            raise WeReadError(response.status_code, "登录状态已失效", reconnect=True)
        if response.status_code == 429:
            raise WeReadError(429, "更新过于频繁，请稍后再试", cooldown=True, cooldown_minutes=6 * 60)
        if response.status_code != 200:
            raise WeReadError(response.status_code, "正文暂时无法获取")
        return extract_mp_content(response.text)

    def validate_credentials(self, source_id: str, credentials: dict | str, ticket: str = "") -> None:
        if isinstance(credentials, dict):
            auth_value = str(credentials.get("auth_header_value") or credentials.get("ticket") or "")
            headers = self._headers(
                str(credentials.get("cookie") or ""),
                auth_value,
                True,
                auth_header_name=str(credentials.get("auth_header_name") or "x-wr-ticket"),
                randstr=str(credentials.get("randstr") or ""),
                wpa=str(credentials.get("wpa") or ""),
                user_agent=str(credentials.get("user_agent") or ""),
                referer=str(credentials.get("referer") or ""),
            )
        else:
            headers = self._headers(str(credentials), ticket, True)
        self._get_articles_page(source_id, 0, headers=headers)
        self.mark_ready()
