from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from playwright.sync_api import sync_playwright

from ..credentials import CredentialVault
from ..providers.weread import BASE_URL, build_mp_reader_url, is_risk_control_message

logger = logging.getLogger(__name__)


def classify_articles_payload(payload: object) -> tuple[str, int, str]:
    if not isinstance(payload, dict):
        return "waiting", 0, ""
    raw_code = payload.get("errCode", payload.get("errcode", 0))
    try:
        code = int(raw_code or 0)
    except (TypeError, ValueError):
        code = 0
    message = str(payload.get("errMsg") or payload.get("errmsg") or "")
    if not code and "reviews" in payload:
        return "ready", 0, message
    if code == -2041:
        return ("cooldown" if is_risk_control_message(message) else "captcha"), code, message
    if code == -2010:
        return "cooldown", code, message
    if code == -2012:
        return "login", code, message
    if code:
        return "error", code, message
    return "waiting", code, message


class WeReadLoginCapture:
    def __init__(self, profile_dir: Path, vault: CredentialVault, validator=None):
        self.profile_dir = profile_dir
        self.vault = vault
        self.validator = validator
        self._cancel = threading.Event()
        self._running = threading.Lock()

    def cancel(self) -> None:
        self._cancel.set()

    @staticmethod
    def _has_login_cookie(context) -> bool:
        cookies = {item["name"]: item["value"] for item in context.cookies(BASE_URL)}
        vid = str(cookies.get("wr_vid") or "").strip()
        skey = str(cookies.get("wr_skey") or "").strip()
        return bool(vid and vid != "0" and skey)

    @staticmethod
    def _credential_snapshot(context, request, source_id: str) -> dict[str, str | float | bool]:
        headers = request.all_headers()
        ticket = headers.get("x-wr-ticket", "")
        wpa = headers.get("x-wrpa-0", "")
        auth_value = ticket or wpa
        cookies = context.cookies(BASE_URL)
        cookie = "; ".join(f"{item['name']}={item['value']}" for item in cookies)
        if not cookie or not auth_value:
            return {}
        return {
            "cookie": cookie,
            "ticket": auth_value,
            "auth_header_name": "x-wr-ticket" if ticket else "x-wrpa-0",
            "auth_header_value": auth_value,
            "randstr": headers.get("x-wr-randstr", ""),
            "wpa": wpa,
            "user_agent": headers.get("user-agent", ""),
            "referer": headers.get("referer", build_mp_reader_url(source_id)),
            "captured_at": time.time(),
        }

    def run(self, source_id: str, emit: Callable[[str, dict], None], timeout_seconds: int = 300) -> dict:
        if not self._running.acquire(blocking=False):
            return {"accepted": False, "message": "\u8fde\u63a5\u7a97\u53e3\u5df2\u7ecf\u6253\u5f00"}
        self._cancel.clear()
        captured: dict[str, str | float | bool] = {}
        state = {"verified": False, "captcha_announced": False, "risk_limited": False, "last_error": "", "payload": None}
        emit("auth.progress", {"stage": "opening", "message": "\u6b63\u5728\u6253\u5f00\u5fae\u4fe1\u8bfb\u4e66"})
        try:
            with sync_playwright() as playwright:
                errors: list[str] = []
                context = None
                for launch in ({"channel": "msedge"}, {"channel": "chrome"}):
                    try:
                        context = playwright.chromium.launch_persistent_context(
                            str(self.profile_dir),
                            headless=False,
                            viewport={"width": 1120, "height": 760},
                            **launch,
                        )
                        break
                    except Exception as exc:
                        errors.append(str(exc))
                if context is None:
                    raise RuntimeError("\u672a\u627e\u5230\u53ef\u7528\u7684 Edge \u6216 Chrome \u6d4f\u89c8\u5668")
                try:
                    page = context.pages[0] if context.pages else context.new_page()

                    def capture_request(request) -> None:
                        if "/web/mp/articles" not in request.url:
                            return
                        try:
                            snapshot = self._credential_snapshot(context, request, source_id)
                            if snapshot:
                                captured.update(snapshot)
                        except Exception:
                            logger.debug("Unable to capture WeRead request credentials", exc_info=True)

                    def capture_response(response) -> None:
                        if "/web/mp/articles" not in response.url:
                            return
                        try:
                            payload = response.json()
                            status, code, message = classify_articles_payload(payload)
                            logger.info(
                                "WeRead browser validation response: status=%s code=%s message=%s",
                                status,
                                code,
                                message[:120],
                            )
                            if status == "ready":
                                snapshot = self._credential_snapshot(context, response.request, source_id)
                                if snapshot:
                                    captured.update(snapshot)
                                    # The captcha/WPA values belong to this successful browser request.
                                    # Keep them only as session metadata; replaying them is what causes
                                    # repeated verification and "sequence repeat" failures.
                                    captured["authorization_consumed"] = True
                                state["payload"] = payload
                                state["verified"] = True
                                return
                            if status == "cooldown":
                                state["risk_limited"] = True
                                state["last_error"] = "微信读书操作过于频繁，请 24 小时后再重新连接"
                                emit("auth.progress", {"stage": "cooldown", "message": state["last_error"]})
                                return
                            if status == "captcha":
                                if not state["captcha_announced"]:
                                    state["captcha_announced"] = True
                                    emit(
                                        "auth.progress",
                                        {
                                            "stage": "captcha",
                                            "message": "请在浏览器中完成人机验证，验证页成功后会自动关闭",
                                        },
                                    )
                                return
                            if status == "login":
                                state["last_error"] = "登录状态已失效"
                            elif status == "error":
                                state["last_error"] = message or f"验证失败（{code}）"
                        except Exception:
                            logger.debug("Unable to inspect WeRead validation response", exc_info=True)

                    def attach_page(browser_page) -> None:
                        browser_page.on("request", capture_request)
                        browser_page.on("response", capture_response)

                    attach_page(page)
                    context.on("page", attach_page)
                    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
                    login_deadline = time.time() + timeout_seconds

                    if not self._has_login_cookie(context):
                        emit(
                            "auth.progress",
                            {"stage": "waiting", "message": "\u8bf7\u5728\u6d4f\u89c8\u5668\u4e2d\u626b\u7801\u767b\u5f55\uff0c\u5e94\u7528\u4f1a\u81ea\u52a8\u7ee7\u7eed"},
                        )
                        login = page.get_by_text("\u767b\u5f55", exact=True)
                        try:
                            if login.count() and login.first.is_visible():
                                login.first.click()
                        except Exception:
                            pass
                        while time.time() < login_deadline and not self._cancel.is_set():
                            if self._has_login_cookie(context):
                                break
                            page.wait_for_timeout(500)

                    if self._cancel.is_set():
                        raise RuntimeError("\u5df2\u53d6\u6d88\u8fde\u63a5")
                    if not self._has_login_cookie(context):
                        raise TimeoutError("\u767b\u5f55\u7b49\u5f85\u8d85\u65f6\uff0c\u8bf7\u91cd\u65b0\u5c1d\u8bd5")

                    emit(
                        "auth.progress",
                        {"stage": "verifying", "message": "\u767b\u5f55\u6210\u529f\uff0c\u6b63\u5728\u9a8c\u8bc1\u516c\u4f17\u53f7\u8bbf\u95ee"},
                    )
                    page.wait_for_timeout(1500)
                    page.goto(build_mp_reader_url(source_id), wait_until="domcontentloaded", timeout=30_000)
                    verification_deadline = max(login_deadline, time.time() + 180)
                    while time.time() < verification_deadline and not self._cancel.is_set() and not state["verified"]:
                        if state["risk_limited"]:
                            break
                        if page.is_closed():
                            remaining_pages = [item for item in context.pages if not item.is_closed()]
                            if not remaining_pages:
                                if state["risk_limited"]:
                                    break
                                raise RuntimeError("人机验证窗口已关闭，请重新连接")
                            page = remaining_pages[-1]
                        try:
                            page.wait_for_timeout(500)
                            body_text = page.locator("body").inner_text(timeout=500)
                        except Exception:
                            continue
                        if is_risk_control_message(body_text):
                            state["risk_limited"] = True
                            state["last_error"] = "微信读书操作过于频繁，请 24 小时后再重新连接"
                            emit("auth.progress", {"stage": "cooldown", "message": state["last_error"]})
                            break

                    if self._cancel.is_set():
                        raise RuntimeError("\u5df2\u53d6\u6d88\u8fde\u63a5")
                    if state["risk_limited"]:
                        raise RuntimeError(state["last_error"])
                    if not state["verified"]:
                        raise TimeoutError(state["last_error"] or "\u4eba\u673a\u9a8c\u8bc1\u7b49\u5f85\u8d85\u65f6\uff0c\u8bf7\u91cd\u65b0\u8fde\u63a5")
                    if not captured:
                        raise RuntimeError("\u672a\u80fd\u4fdd\u5b58\u5fae\u4fe1\u8bfb\u4e66\u767b\u5f55\u72b6\u6001")

                    # The successful browser response is the source of truth. Do not replay
                    # the signed request immediately; that creates a second security challenge.
                    self.vault.save("weread", captured)
                    emit("auth.completed", {"message": "微信读书已连接", "source_id": source_id})
                    return {
                        "ok": True,
                        "message": "微信读书已连接",
                        "articles_payload": state["payload"],
                    }
                finally:
                    context.close()
        finally:
            self._running.release()
