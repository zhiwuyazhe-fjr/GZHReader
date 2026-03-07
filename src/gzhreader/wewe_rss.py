from __future__ import annotations

from pathlib import Path
import subprocess

import httpx

from .config import WeWeRSSConfig


SQLITE_COMPOSE = """services:
  app:
    image: ${WEWE_RSS_IMAGE:-cooderl/wewe-rss:latest}
    restart: unless-stopped
    ports:
      - "${WEWE_RSS_PORT:-4000}:4000"
    environment:
      DATABASE_TYPE: sqlite
      DATABASE_URL: file:/app/data/wewe-rss.db?connection_limit=1
      AUTH_CODE: ${WEWE_RSS_AUTH_CODE}
      SERVER_ORIGIN_URL: ${WEWE_RSS_SERVER_ORIGIN_URL}
    volumes:
      - ./data:/app/data
"""

MYSQL_COMPOSE = """services:
  mysql:
    image: mysql:8.4
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: ${MYSQL_DATABASE}
      MYSQL_USER: ${MYSQL_USER}
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -u$$MYSQL_USER -p$$MYSQL_PASSWORD --silent"]
      interval: 5s
      timeout: 5s
      retries: 30
      start_period: 20s
    volumes:
      - mysql_data:/var/lib/mysql
  app:
    image: ${WEWE_RSS_IMAGE:-cooderl/wewe-rss:latest}
    restart: unless-stopped
    depends_on:
      mysql:
        condition: service_healthy
    ports:
      - "${WEWE_RSS_PORT:-4000}:4000"
    environment:
      DATABASE_TYPE: mysql
      DATABASE_URL: mysql://${MYSQL_USER}:${MYSQL_PASSWORD}@mysql:3306/${MYSQL_DATABASE}
      AUTH_CODE: ${WEWE_RSS_AUTH_CODE}
      SERVER_ORIGIN_URL: ${WEWE_RSS_SERVER_ORIGIN_URL}

volumes:
  mysql_data:
"""

ENV_TEMPLATE = """WEWE_RSS_IMAGE={image}
WEWE_RSS_AUTH_CODE={auth_code}
WEWE_RSS_SERVER_ORIGIN_URL={server_origin_url}
WEWE_RSS_PORT={port}
MYSQL_ROOT_PASSWORD=change_me_root
MYSQL_DATABASE=wewe_rss
MYSQL_USER=wewe_rss
MYSQL_PASSWORD=change_me_user
"""


class WeWeRSSManager:
    def __init__(self, config: WeWeRSSConfig) -> None:
        self.config = config
        self.service_dir = Path(config.service_dir)

    def ensure_scaffold(self, force: bool = False) -> list[Path]:
        self.service_dir.mkdir(parents=True, exist_ok=True)
        (self.service_dir / "data").mkdir(parents=True, exist_ok=True)
        (self.service_dir / "mysql").mkdir(parents=True, exist_ok=True)

        env_path = self.service_dir / ".env"
        sqlite_path = self.service_dir / "docker-compose.sqlite.yml"
        mysql_path = self.service_dir / "docker-compose.mysql.yml"
        active_path = self.service_dir / "docker-compose.yml"

        if force or not env_path.exists():
            env_path.write_text(
                ENV_TEMPLATE.format(
                    image=self.config.image,
                    auth_code=self.config.auth_code,
                    server_origin_url=self.config.server_origin_url,
                    port=self.config.port,
                ),
                encoding="utf-8",
            )
        sqlite_path.write_text(SQLITE_COMPOSE, encoding="utf-8")
        mysql_path.write_text(MYSQL_COMPOSE, encoding="utf-8")
        selected = sqlite_path if self.config.compose_variant == "sqlite" else mysql_path
        active_path.write_text(selected.read_text(encoding="utf-8"), encoding="utf-8")
        return [env_path, sqlite_path, mysql_path, active_path]

    def check_docker(self) -> tuple[bool, str]:
        try:
            version = subprocess.run(["docker", "--version"], capture_output=True, text=True, check=True)
            info = subprocess.run(["docker", "info"], capture_output=True, text=True)
        except Exception as exc:
            return False, f"Docker 不可用: {exc}"
        if info.returncode != 0:
            detail = (info.stderr or info.stdout or "Docker 引擎未启动").strip()
            return False, f"Docker 已安装，但引擎不可用: {detail}"
        return True, version.stdout.strip()

    def check_service(self) -> tuple[bool, str]:
        try:
            response = httpx.get(self.config.base_url, timeout=8, follow_redirects=True)
            return True, f"服务可访问，HTTP {response.status_code}"
        except Exception as exc:
            return False, f"服务不可访问: {exc}"

    def up(self) -> str:
        self.ensure_scaffold(force=False)
        return self._compose_command(["up", "-d"])

    def down(self) -> str:
        self.ensure_scaffold(force=False)
        return self._compose_command(["down"])

    def logs(self) -> str:
        self.ensure_scaffold(force=False)
        return self._compose_command(["logs", "--tail", "120"])

    def _compose_command(self, args: list[str]) -> str:
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "docker-compose.yml",
                *args,
            ],
            cwd=self.service_dir,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "docker compose 执行失败").strip()
            raise RuntimeError(detail)
        return completed.stdout.strip() or completed.stderr.strip() or "ok"
