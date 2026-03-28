from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .config import RSSServiceConfig
from .platform_utils import hidden_process_kwargs, open_web_url
from .runtime_paths import (
    RuntimePaths,
    ensure_runtime_dirs,
    get_console_executable_path,
    get_runtime_paths,
    is_frozen_app,
)

LOCAL_ACCOUNT_TOKEN_PREFIX = "gzh_local_"
RECONNECT_MESSAGE = "删除账号后，重新扫码添加"


@dataclass(slots=True)
class RSSServiceRuntimeStatus:
    runtime_ok: bool
    runtime_detail: str
    process_ok: bool
    process_detail: str
    web_ok: bool
    web_detail: str
    admin_url: str
    feed_url: str


@dataclass(slots=True)
class RSSRefreshResult:
    completed: bool
    refreshed_count: int
    total_count: int
    budget_remaining: int | None
    reason_code: str
    reason: str
    detail: str


class BundledRSSServiceManager:
    def __init__(self, config: RSSServiceConfig, runtime_paths: RuntimePaths | None = None) -> None:
        self.config = config
        self.runtime_paths = ensure_runtime_dirs(runtime_paths or get_runtime_paths())

    @property
    def admin_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/dash"

    @property
    def feed_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/feeds/all.atom"

    @property
    def bridge_url(self) -> str:
        return f"http://127.0.0.1:{self.config.bridge_port}"

    def status_snapshot(self) -> RSSServiceRuntimeStatus:
        runtime_ok, runtime_detail = self.check_runtime()
        process_ok, process_detail = self.check_process()
        web_ok, web_detail = self.check_service()
        return RSSServiceRuntimeStatus(
            runtime_ok=runtime_ok,
            runtime_detail=runtime_detail,
            process_ok=process_ok,
            process_detail=process_detail,
            web_ok=web_ok,
            web_detail=web_detail,
            admin_url=self.admin_url,
            feed_url=self.feed_url,
        )

    def check_runtime(self) -> tuple[bool, str]:
        runtime_root = self._resolve_runtime_root()
        if runtime_root is None:
            return False, "未找到 bundled wewe-rss 运行时，请先执行 scripts/build_wewe_rss.ps1"

        entrypoint = runtime_root / "apps" / "server" / "dist" / "main.js"
        client_index = runtime_root / "apps" / "server" / "client" / "index.hbs"
        if not entrypoint.exists():
            return False, f"缺少 server 入口：{entrypoint}"
        if not client_index.exists():
            return False, f"缺少 web 客户端产物：{client_index}"

        node_exe = self._find_node_executable(runtime_root)
        if node_exe is None:
            return False, "未找到 bundled node.exe，也没有检测到系统 Node.js"

        return True, f"运行时已就绪：{runtime_root}"

    def check_process(self) -> tuple[bool, str]:
        pid = self._read_pid()
        if pid is not None and self._pid_exists(pid):
            return True, f"本地公众号服务正在运行（pid={pid}）"
        if pid is not None and not self._pid_exists(pid):
            self._remove_pid_file()
            return False, "检测到过期 pid 文件，已自动清理"
        if self._check_http_ok():
            return True, "本地公众号服务已经响应，但没有记录 pid"
        return False, "本地公众号服务尚未启动"

    def check_service(self) -> tuple[bool, str]:
        try:
            response = httpx.get(self.config.base_url, timeout=5.0, follow_redirects=True)
        except Exception as exc:
            return False, f"服务暂时不可访问：{exc}"
        return True, f"服务可访问，HTTP {response.status_code}"

    def start(self) -> str:
        runtime_ok, runtime_detail = self.check_runtime()
        if not runtime_ok:
            raise RuntimeError(runtime_detail)

        if self._check_http_ok():
            return "本地公众号服务已经在运行"

        runtime_root = self._resolve_runtime_root()
        if runtime_root is None:
            raise RuntimeError("未找到 bundled wewe-rss 运行时")

        node_exe = self._find_node_executable(runtime_root)
        if node_exe is None:
            raise RuntimeError("未找到可用的 Node.js 运行时")

        db_path = Path(self.config.data_dir).expanduser().resolve() / "wewe-rss.db"
        self._start_bridge()
        self._apply_sqlite_migrations(runtime_root, db_path)
        self._mark_legacy_accounts_for_reconnect(db_path)

        entrypoint = runtime_root / "apps" / "server" / "dist" / "main.js"
        log_file = Path(self.config.log_file).expanduser().resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(self._build_runtime_env(db_path))

        with log_file.open("a", encoding="utf-8") as stream:
            process = subprocess.Popen(
                [str(node_exe), str(entrypoint)],
                cwd=runtime_root,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                env=env,
                **hidden_process_kwargs(),
            )

        self._write_pid(process.pid)
        if not self._wait_until_ready(timeout_seconds=15.0):
            self._stop_bridge()
            if process.poll() is not None:
                raise RuntimeError(f"服务启动失败，退出码={process.returncode}。请查看日志：{log_file}")
            raise RuntimeError(f"服务启动超时。请查看日志：{log_file}")

        return f"本地公众号服务已启动：{self.config.base_url}"

    def stop(self) -> str:
        pid = self._read_pid()
        if pid is None:
            self._stop_bridge()
            if self._check_http_ok():
                return "服务正在响应，但没有本地 pid 文件，因此没有执行停止"
            return "本地公众号服务当前未运行"

        self._kill_pid(pid)
        self._remove_pid_file()
        self._stop_bridge()
        return "本地公众号服务已停止"

    def restart(self) -> str:
        self.stop()
        return self.start()

    def logs(self, tail: int = 120) -> str:
        log_file = Path(self.config.log_file).expanduser().resolve()
        if not log_file.exists():
            return "暂无服务日志"
        lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        snippet = lines[-tail:]
        return "\n".join(snippet) if snippet else "暂无服务日志"

    def open_admin(self, *, return_to: str = "") -> str:
        target_url = self.admin_url
        if return_to.strip():
            parsed = urlsplit(target_url)
            query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query_items["return_to"] = return_to.strip()
            target_url = urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    urlencode(query_items),
                    parsed.fragment,
                )
            )
        open_web_url(target_url)
        return f"已尝试打开 {target_url}"

    def refresh_all_feeds(self) -> RSSRefreshResult:
        try:
            response = httpx.post(
                f"{self.config.base_url.rstrip('/')}/internal/refresh-all",
                timeout=None,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"本地公众号后台暂时无法完成刷新：{exc}") from exc

        return RSSRefreshResult(
            completed=bool(payload.get("completed")),
            refreshed_count=int(payload.get("refreshedCount") or 0),
            total_count=int(payload.get("totalCount") or 0),
            budget_remaining=(
                int(payload["budgetRemaining"])
                if payload.get("budgetRemaining") is not None
                else None
            ),
            reason_code=str(payload.get("reasonCode") or ""),
            reason=str(payload.get("reason") or ""),
            detail=str(payload.get("detail") or ""),
        )

    def _build_runtime_env(self, db_path: Path) -> dict[str, str]:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return {
            "NODE_ENV": "production",
            "HOST": self.config.host,
            "PORT": str(self.config.port),
            "DATABASE_TYPE": "sqlite",
            "DATABASE_URL": f"file:{db_path.as_posix()}",
            "AUTH_CODE": "",
            "SERVER_ORIGIN_URL": self.config.base_url,
            "PLATFORM_URL": self.bridge_url,
        }

    def _resolve_runtime_root(self) -> Path | None:
        source = self.runtime_paths.bundled_wewe_rss_source_dir
        if not is_frozen_app() and (source / "apps" / "server" / "dist" / "main.js").exists():
            return source

        packaged = self.runtime_paths.bundled_wewe_rss_runtime_dir
        if packaged.exists():
            return packaged

        if (source / "apps" / "server" / "dist" / "main.js").exists():
            return source
        return None

    def _find_node_executable(self, runtime_root: Path) -> Path | None:
        for candidate in (runtime_root / "node.exe", runtime_root / "node", runtime_root / ".node" / "node.exe"):
            if candidate.exists():
                return candidate
        system_node = shutil.which("node")
        return Path(system_node) if system_node else None

    def _apply_sqlite_migrations(self, runtime_root: Path, db_path: Path) -> None:
        migrations_root = self._resolve_sqlite_migrations_root(runtime_root)
        if not migrations_root.exists():
            return

        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS _gzhreader_sqlite_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM _gzhreader_sqlite_migrations"
                ).fetchall()
            }

            for migration_dir in sorted(migrations_root.iterdir()):
                migration_sql = migration_dir / "migration.sql"
                if not migration_dir.is_dir() or not migration_sql.exists():
                    continue
                if migration_dir.name in applied:
                    continue

                try:
                    connection.executescript(migration_sql.read_text(encoding="utf-8"))
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if "already exists" not in message and "duplicate column name" not in message:
                        raise
                connection.execute(
                    "INSERT INTO _gzhreader_sqlite_migrations(name) VALUES (?)",
                    (migration_dir.name,),
                )
            connection.commit()

    def _mark_legacy_accounts_for_reconnect(self, db_path: Path) -> None:
        if not db_path.exists():
            return
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                UPDATE accounts
                SET status = 0,
                    consecutive_auth_failures = CASE
                        WHEN consecutive_auth_failures < 3 THEN 3
                        ELSE consecutive_auth_failures
                    END,
                    cooldown_until = NULL,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE token IS NOT NULL
                  AND token != ''
                  AND token NOT LIKE ?
                """,
                (RECONNECT_MESSAGE, f"{LOCAL_ACCOUNT_TOKEN_PREFIX}%"),
            )
            connection.commit()

    def _resolve_sqlite_migrations_root(self, runtime_root: Path) -> Path:
        return runtime_root / "apps" / "server" / "prisma" / "migrations"

    def _wait_until_ready(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._check_http_ok():
                return True
            time.sleep(0.5)
        return False

    def _check_http_ok(self) -> bool:
        try:
            response = httpx.get(self.config.base_url, timeout=1.5, follow_redirects=True)
        except Exception:
            return False
        return response.status_code < 500

    def _check_bridge_ok(self) -> bool:
        try:
            response = httpx.get(f"{self.bridge_url}/healthz", timeout=1.5)
        except Exception:
            return False
        return response.status_code == 200

    def _start_bridge(self) -> None:
        if self._check_bridge_ok():
            return

        pid = self._read_bridge_pid()
        if pid is not None and not self._pid_exists(pid):
            self._remove_bridge_pid_file()

        log_file = self._bridge_log_path()
        log_file.parent.mkdir(parents=True, exist_ok=True)

        with log_file.open("a", encoding="utf-8") as stream:
            process = subprocess.Popen(
                self._build_bridge_command(),
                cwd=self.runtime_paths.state_dir,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                **hidden_process_kwargs(),
            )

        self._write_bridge_pid(process.pid)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self._check_bridge_ok():
                return
            if process.poll() is not None:
                raise RuntimeError(
                    f"本地会话桥启动失败，退出码={process.returncode}。请查看日志：{log_file}"
                )
            time.sleep(0.4)

        raise RuntimeError(f"本地会话桥启动超时。请查看日志：{log_file}")

    def _stop_bridge(self) -> None:
        pid = self._read_bridge_pid()
        if pid is None:
            return
        self._kill_pid(pid)
        self._remove_bridge_pid_file()

    def _build_bridge_command(self) -> list[str]:
        args = [
            "bridge-serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.config.bridge_port),
            "--remote-url",
            self.config.remote_platform_url,
            "--session-store",
            str(self._session_store_path()),
        ]
        if is_frozen_app():
            executable = get_console_executable_path() or Path(sys.executable).resolve()
            return [str(executable), *args]
        return [str(Path(sys.executable).resolve()), "-m", "gzhreader", *args]

    def _session_store_path(self) -> Path:
        return self.runtime_paths.rss_service_dir / "weread-sessions.json"

    def _bridge_log_path(self) -> Path:
        return self.runtime_paths.logs_dir / "weread-bridge.log"

    def _bridge_pid_path(self) -> Path:
        return self.runtime_paths.rss_service_dir / "weread-bridge.pid"

    def _write_pid(self, pid: int) -> None:
        self.runtime_paths.rss_service_pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_paths.rss_service_pid_file.write_text(str(pid), encoding="utf-8")

    def _read_pid(self) -> int | None:
        pid_file = self.runtime_paths.rss_service_pid_file
        if not pid_file.exists():
            return None
        raw = pid_file.read_text(encoding="utf-8").strip()
        if not raw.isdigit():
            self._remove_pid_file()
            return None
        return int(raw)

    def _remove_pid_file(self) -> None:
        try:
            self.runtime_paths.rss_service_pid_file.unlink()
        except FileNotFoundError:
            pass

    def _write_bridge_pid(self, pid: int) -> None:
        self._bridge_pid_path().parent.mkdir(parents=True, exist_ok=True)
        self._bridge_pid_path().write_text(str(pid), encoding="utf-8")

    def _read_bridge_pid(self) -> int | None:
        pid_file = self._bridge_pid_path()
        if not pid_file.exists():
            return None
        raw = pid_file.read_text(encoding="utf-8").strip()
        if not raw.isdigit():
            self._remove_bridge_pid_file()
            return None
        return int(raw)

    def _remove_bridge_pid_file(self) -> None:
        try:
            self._bridge_pid_path().unlink()
        except FileNotFoundError:
            pass

    def _kill_pid(self, pid: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                **hidden_process_kwargs(),
            )
            return
        try:
            os.kill(pid, 15)
        except OSError:
            pass

    def _pid_exists(self, pid: int) -> bool:
        if os.name == "nt":
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                check=False,
                **hidden_process_kwargs(),
            )
            return completed.returncode == 0 and str(pid) in completed.stdout
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
