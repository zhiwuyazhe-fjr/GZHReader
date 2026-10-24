from __future__ import annotations

import logging
import random
import threading
from datetime import datetime, timedelta
from typing import Callable

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, storage, sync: Callable[[], dict], briefing: Callable[[], dict], emit):
        self.storage = storage
        self.sync = sync
        self.briefing = briefing
        self.emit = emit
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._run_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._briefing_retry_at: datetime | None = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.reschedule(reset=False)
            self.thread = threading.Thread(target=self._loop, name="gzhreader-scheduler", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self.stop_event.set()
            self.wake_event.set()
            thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)

    def reschedule(self, reset: bool = True) -> dict:
        settings = self.storage.settings()
        minutes = int(settings.get("refresh_minutes", 60))
        if minutes <= 0 or settings.get("refresh_paused", False):
            next_refresh = ""
        else:
            current = str(settings.get("next_refresh_at") or "")
            if reset or not current:
                next_refresh = (self._now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
            else:
                next_refresh = current
        self.storage.update_settings({"next_refresh_at": next_refresh})
        self.wake_event.set()
        return self.status()

    def status(self) -> dict:
        settings = self.storage.settings()
        return {
            "running": bool(self.thread and self.thread.is_alive()),
            "paused": bool(settings.get("refresh_paused", False)),
            "next_refresh_at": settings.get("next_refresh_at", ""),
            "briefing_enabled": settings.get("briefing_enabled", True),
            "briefing_time": settings.get("briefing_time", "21:30"),
        }

    def run_now(self) -> dict:
        if self.stop_event.is_set():
            return {"accepted": False, "message": "服务正在关闭"}
        if not self._run_lock.acquire(blocking=False):
            return {"accepted": False, "message": "正在刷新，请稍候"}
        try:
            return self.sync()
        finally:
            self._run_lock.release()

    def briefing_due_now(self) -> bool:
        settings = self.storage.settings()
        now = self._now()
        today = now.date()
        if (
            not settings.get("briefing_enabled", True)
            or self.storage.briefing(today)
            or settings.get("briefing_skip_day") == today.isoformat()
        ):
            return False
        try:
            hour, minute = map(int, str(settings.get("briefing_time", "21:30")).split(":"))
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (TypeError, ValueError):
            return False
        return now >= scheduled

    def _now(self) -> datetime:
        return self.storage.local_now()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            self.wake_event.clear()
            try:
                self._tick()
            except Exception:
                logger.exception("Scheduled task failed")
                try:
                    self.emit("core.error", {"message": "后台任务暂时未能完成"})
                except Exception:
                    pass
            if not self.stop_event.is_set():
                self.wake_event.wait(20)

    def _tick(self) -> None:
        settings = self.storage.settings()
        if self.stop_event.is_set():
            return
        now = self._now()
        next_value = str(settings.get("next_refresh_at") or "")
        if int(settings.get("refresh_minutes", 60)) > 0 and not settings.get("refresh_paused", False) and next_value:
            try:
                next_refresh = datetime.fromisoformat(next_value)
                if next_refresh.tzinfo is None:
                    next_refresh = next_refresh.replace(tzinfo=now.tzinfo)
            except ValueError:
                self.reschedule(reset=True)
                next_refresh = None
            if next_refresh and now >= next_refresh:
                result = self.run_now()
                current = self.storage.settings()
                if (
                    not self.stop_event.is_set()
                    and result.get("accepted") is not False
                    and current.get("next_refresh_at") == next_value
                    and not current.get("refresh_paused", False)
                    and int(current.get("refresh_minutes", 60)) > 0
                ):
                    minutes = int(current.get("refresh_minutes", 60))
                    next_time = self._now() + timedelta(minutes=minutes, seconds=random.randint(-300, 300))
                    self.storage.update_settings({"next_refresh_at": next_time.isoformat(timespec="seconds")})
        if (
            not self.stop_event.is_set()
            and (self._briefing_retry_at is None or self._now() >= self._briefing_retry_at)
            and self.briefing_due_now()
        ):
            try:
                result = self.briefing()
            except Exception:
                self._briefing_retry_at = self._now() + timedelta(minutes=5)
                raise
            self._briefing_retry_at = None
            self.emit("briefing.ready", {"day": result["day"], "file_path": result["file_path"]})
