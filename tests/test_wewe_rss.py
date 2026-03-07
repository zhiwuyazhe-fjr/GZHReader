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
