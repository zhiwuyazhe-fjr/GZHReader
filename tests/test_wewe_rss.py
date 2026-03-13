from gzhreader.config import WeWeRSSConfig
from gzhreader.wewe_rss import WeWeRSSManager


def test_wewe_rss_manager_writes_scaffold(tmp_path) -> None:
    manager = WeWeRSSManager(
        WeWeRSSConfig(
            service_dir=str(tmp_path / "wewe-rss"),
            compose_variant="sqlite",
            auth_code="123567",
        )
    )

    generated = manager.ensure_scaffold(force=False)

    assert (tmp_path / "wewe-rss" / "docker-compose.yml").exists()
    assert (tmp_path / "wewe-rss" / ".env").exists()
    assert len(generated) == 4


def test_wewe_rss_manager_refreshes_env_when_auth_code_changes(tmp_path) -> None:
    service_dir = tmp_path / "wewe-rss"
    manager = WeWeRSSManager(
        WeWeRSSConfig(
            service_dir=str(service_dir),
            compose_variant="sqlite",
            auth_code="123567",
        )
    )
    manager.ensure_scaffold(force=False)

    updated_manager = WeWeRSSManager(
        WeWeRSSConfig(
            service_dir=str(service_dir),
            compose_variant="sqlite",
            auth_code="654321",
        )
    )

    updated_manager.ensure_scaffold(force=False)

    env_text = (service_dir / ".env").read_text(encoding="utf-8")
    assert "WEWE_RSS_AUTH_CODE=654321" in env_text
