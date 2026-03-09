from gzhreader import __version__
from gzhreader.config import AppConfig, ensure_config, load_config, save_config


def test_legacy_accounts_migrate_to_source(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
accounts:
  - name: 新智元
    wechat_id: gh_test
    active: true
    order: 1
output:
  html_archive_dir: ./output/html
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.source.mode == "aggregate"
    assert cfg.source.url == "http://localhost:4000/feeds/all.atom"
    assert cfg.output.raw_archive_dir == "./output/html"
    assert cfg.output.save_raw_html is False
    assert cfg.article_fetch.enabled is True
    assert cfg.article_fetch.browser_channel_order == ["msedge", "chrome"]


def test_legacy_feeds_migrates_and_creates_backup(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
feeds:
  - name: 新智元
    url: http://localhost:4000/feeds/all.atom
    active: true
    order: 1
""".strip(),
        encoding="utf-8",
    )

    cfg = ensure_config(config_path)

    assert cfg.source.url == "http://localhost:4000/feeds/all.atom"
    assert (tmp_path / "config.yaml.bak").exists()
    migrated_text = config_path.read_text(encoding="utf-8")
    assert "source:" in migrated_text
    assert "feeds:" not in migrated_text


def test_legacy_rss_limit_migrates_and_creates_backup(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source:
  mode: aggregate
  url: http://localhost:4000/feeds/all.atom
rss:
  max_articles_per_feed: 30
""".strip(),
        encoding="utf-8",
    )

    cfg = ensure_config(config_path)

    assert cfg.rss.daily_article_limit == 30
    assert (tmp_path / "config.yaml.bak").exists()
    migrated_text = config_path.read_text(encoding="utf-8")
    assert "daily_article_limit: 30" in migrated_text
    assert "max_articles_per_feed" not in migrated_text


def test_daily_article_limit_supports_all(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source:
  mode: aggregate
  url: http://localhost:4000/feeds/all.atom
rss:
  daily_article_limit: all
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.rss.daily_article_limit == "all"


def test_save_and_reload_roundtrip(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    cfg = AppConfig()
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.model_dump() == cfg.model_dump()
    assert loaded.wewe_rss.compose_variant == "mysql"
    assert loaded.output.save_raw_html is False
    assert loaded.rss.daily_article_limit == 20


def test_default_user_agent_follows_package_version() -> None:
    assert AppConfig().rss.user_agent == f"GZHReader/{__version__}"
