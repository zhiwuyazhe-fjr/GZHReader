from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from playwright.sync_api import sync_playwright

from ..credentials import CredentialVault
from ..providers.weread import BASE_URL, build_mp_reader_url, is_risk_control_message

logger = logging.getLogger(__name__)


def browser_executable_candidates() -> list[Path]:
    """Return installed Chromium browsers without asking Playwright to launch them."""
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("PROGRAMFILES")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe")
    if program_files:
        candidates.extend(
            [
                Path(program_files) / "Microsoft/Edge/Application/msedge.exe",
                Path(program_files) / "Google/Chrome/Application/chrome.exe",
            ]
        )
    if local_app_data:
        candidates.extend(
            [
                Path(local_app_data) / "Microsoft/Edge/Application/msedge.exe",
                Path(local_app_data) / "Google/Chrome/Application/chrome.exe",
            ]
        )
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Google/Chrome/Application/chrome.exe")

    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen and candidate.is_file():
            seen.add(key)
            result.append(candidate)
    return result


def build_browser_command(executable: Path, profile_dir: Path, debug_port: int) -> list[str]:
    """Build a minimal, user-visible browser command for manual verification."""
    return [
        str(executable),
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        "--window-size=1120,760",
        "about:blank",
    ]


def reserve_debug_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def stop_browser_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


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
                browser = None
                browser_process = None
                context = None
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                for executable in browser_executable_candidates():
                    candidate_process = None
                    try:
                        debug_port = reserve_debug_port()
                        candidate_process = subprocess.Popen(
                            build_browser_command(executable, self.profile_dir, debug_port),
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        endpoint = f"http://127.0.0.1:{debug_port}"
                        connect_deadline = time.time() + 15
                        last_error = ""
                        while time.time() < connect_deadline:
                            if candidate_process.poll() is not None:
                                raise RuntimeError(
                                    f"浏览器提前退出（代码 {candidate_process.returncode}）"
                                )
                            try:
                                browser = playwright.chromium.connect_over_cdp(endpoint, timeout=1_500)
                                break
                            except Exception as exc:
                                last_error = str(exc)
                                time.sleep(0.2)
                        if browser is None:
                            raise RuntimeError(last_error or "无法连接浏览器")
                        if not browser.contexts:
                            raise RuntimeError("浏览器没有可用的用户上下文")
                        browser_process = candidate_process
                        context = browser.contexts[0]
                        logger.info("Connected to user browser for verification: %s", executable.name)
                        break
                    except Exception as exc:
                        errors.append(f"{executable.name}: {exc}")
                        if browser is not None:
                            try:
                                browser.close()
                            except Exception:
                                pass
                            browser = None
                        if candidate_process is not None:
                            stop_browser_process(candidate_process)
                if context is None:
                    details = "; ".join(errors[-2:])
                    raise RuntimeError(
                        "未能启动本机 Edge 或 Chrome 浏览器"
                        + (f"：{details}" if details else "")
                    )
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

                    for existing_page in context.pages:
                        attach_page(existing_page)
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
                    if browser is not None:
                        try:
                            browser.close()
                        except Exception:
                            logger.debug("Unable to close browser connection", exc_info=True)
                    if browser_process is not None:
                        stop_browser_process(browser_process)
        finally:
            self._running.release()
