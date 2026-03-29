from __future__ import annotations

import os
import shutil
import socket
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
RECONNECT_MESSAGE = "删除账号后重新扫码登录"
WEWE_RSS_HEALTH_SERVICE_NAME = "wewe-rss"
PORT_SCAN_LIMIT = 20


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

    @property
    def health_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/healthz"

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

        server_root = self._resolve_server_root(runtime_root)
        if server_root is None:
            return False, f"缺少 server 入口：{runtime_root}"

        entrypoint = server_root / "dist" / "main.js"
        client_index = server_root / "client" / "index.hbs"
        if not entrypoint.exists():
            return False, f"缺少 server 入口：{entrypoint}"
        if not client_index.exists():
            return False, f"缺少 web 客户端产物：{client_index}"

        node_exe = self._find_node_executable(runtime_root, server_root)
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
        return self._probe_service_health(timeout_seconds=5.0)

    def start(self) -> str:
        runtime_ok, runtime_detail = self.check_runtime()
        if not runtime_ok:
            raise RuntimeError(runtime_detail)

        if self._check_http_ok():
            return "本地公众号服务已经在运行"

        port_switch_detail = self._ensure_service_port_available()

        runtime_root = self._resolve_runtime_root()
        if runtime_root is None:
            raise RuntimeError("未找到 bundled wewe-rss 运行时")

        server_root = self._resolve_server_root(runtime_root)
        if server_root is None:
            raise RuntimeError("未找到 bundled wewe-rss server 入口")

        node_exe = self._find_node_executable(runtime_root, server_root)
        if node_exe is None:
            raise RuntimeError("未找到可用的 Node.js 运行时")

        db_path = Path(self.config.data_dir).expanduser().resolve() / "wewe-rss.db"
        self._start_bridge()
        self._apply_sqlite_migrations(server_root, db_path)
        self._mark_legacy_accounts_for_reconnect(db_path)

        entrypoint = server_root / "dist" / "main.js"
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
            self._remove_pid_file()
            if process.poll() is not None:
                raise RuntimeError(f"服务启动失败，退出码={process.returncode}。请查看日志：{log_file}")
            raise RuntimeError(f"服务启动超时。请查看日志：{log_file}")

        if port_switch_detail:
            return f"{port_switch_detail}，本地公众号服务已启动：{self.config.base_url}"
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
        if not self._check_http_ok():
            self.start()

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

    def _browser_host(self) -> str:
        host = self.config.host.strip()
        if host in {"", "0.0.0.0", "::"}:
            return "127.0.0.1"
        return host

    def _update_service_port(self, port: int) -> None:
        self.config.port = port
        self.config.base_url = f"http://{self._browser_host()}:{port}"

    def _resolve_runtime_root(self) -> Path | None:
        source = self.runtime_paths.bundled_wewe_rss_source_dir
        if not is_frozen_app() and self._resolve_server_root(source):
            return source

        packaged = self.runtime_paths.bundled_wewe_rss_runtime_dir
        if packaged.exists() and self._resolve_server_root(packaged):
            return packaged

        if self._resolve_server_root(source):
            return source
        return None

    def _resolve_server_root(self, runtime_root: Path) -> Path | None:
        direct_root = runtime_root
        if (direct_root / "dist" / "main.js").exists():
            return direct_root

        nested_root = runtime_root / "apps" / "server"
        if (nested_root / "dist" / "main.js").exists():
            return nested_root

        return None

    def _find_node_executable(self, runtime_root: Path, server_root: Path | None = None) -> Path | None:
        candidates: list[Path] = []
        if server_root is not None:
            candidates.extend((server_root / "node.exe", server_root / "node", server_root / ".node" / "node.exe"))
        candidates.extend((runtime_root / "node.exe", runtime_root / "node", runtime_root / ".node" / "node.exe"))

        for candidate in candidates:
            if candidate.exists():
                return candidate
        system_node = shutil.which("node")
        return Path(system_node) if system_node else None

    def _apply_sqlite_migrations(self, server_root: Path, db_path: Path) -> None:
        _ = server_root
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as connection:
            self._ensure_sqlite_table(
                connection,
                "accounts",
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY NOT NULL,
                    token TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status INTEGER NOT NULL DEFAULT 1,
                    consecutive_auth_failures INTEGER NOT NULL DEFAULT 0,
                    daily_request_count INTEGER NOT NULL DEFAULT 0,
                    daily_request_date TEXT,
                    cooldown_until INTEGER,
                    last_error TEXT,
                    last_success_at DATETIME,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            self._ensure_sqlite_table(
                connection,
                "feeds",
                """
                CREATE TABLE IF NOT EXISTS feeds (
                    id TEXT PRIMARY KEY NOT NULL,
                    mp_name TEXT NOT NULL,
                    mp_cover TEXT NOT NULL,
                    mp_intro TEXT NOT NULL,
                    status INTEGER NOT NULL DEFAULT 1,
                    sync_time INTEGER NOT NULL DEFAULT 0,
                    update_time INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    has_history INTEGER DEFAULT 1
                )
                """,
            )
            self._ensure_sqlite_table(
                connection,
                "articles",
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY NOT NULL,
                    mp_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    pic_url TEXT NOT NULL,
                    publish_time INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )

            self._ensure_sqlite_columns(
                connection,
                "accounts",
                {
                    "consecutive_auth_failures": "INTEGER NOT NULL DEFAULT 0",
                    "daily_request_count": "INTEGER NOT NULL DEFAULT 0",
                    "daily_request_date": "TEXT",
                    "cooldown_until": "INTEGER",
                    "last_error": "TEXT",
                    "last_success_at": "DATETIME",
                },
            )
            self._ensure_sqlite_columns(
                connection,
                "feeds",
                {
                    "has_history": "INTEGER DEFAULT 1",
                },
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

    def _ensure_sqlite_table(self, connection: sqlite3.Connection, table_name: str, ddl: str) -> None:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if table_name in existing_tables:
            return
        connection.execute(ddl)

    def _ensure_sqlite_columns(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        columns: dict[str, str],
    ) -> None:
        existing_columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name, column_definition in columns.items():
            if column_name in existing_columns:
                continue
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )
            existing_columns.add(column_name)

    def _wait_until_ready(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._check_http_ok():
                return True
            time.sleep(0.5)
        return False

    def _check_http_ok(self) -> bool:
        ok, _ = self._probe_service_health(timeout_seconds=1.5)
        return ok

    def _probe_service_health(self, *, timeout_seconds: float) -> tuple[bool, str]:
        try:
            response = httpx.get(self.health_url, timeout=timeout_seconds, follow_redirects=True)
        except Exception as exc:
            return False, f"服务暂时不可访问：{exc}"

        if response.status_code != 200:
            return False, f"健康检查未通过，HTTP {response.status_code}"

        try:
            payload = response.json()
        except Exception:
            return False, "健康检查返回了非法响应"

        if payload.get("ok") is not True or payload.get("service") != WEWE_RSS_HEALTH_SERVICE_NAME:
            return False, "当前端口上的响应不是 GZHReader 公众号后台"

        return True, "服务可访问，健康检查已通过"

    def _can_bind_port(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                return False
        return True

    def _ensure_service_port_available(self) -> str:
        if self._can_bind_port(self.config.host, self.config.port):
            return ""

        original_port = self.config.port
        for candidate in range(original_port + 1, original_port + PORT_SCAN_LIMIT):
            if self._can_bind_port(self.config.host, candidate):
                self._update_service_port(candidate)
                return f"端口 {original_port} 已被其他程序占用，已自动切换到 {candidate}"

        raise RuntimeError(
            f"端口 {original_port} 已被占用，而且在 {original_port}-{original_port + PORT_SCAN_LIMIT - 1} 之间没找到可用端口。请在设置里改一个空闲端口后重试"
        )

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
