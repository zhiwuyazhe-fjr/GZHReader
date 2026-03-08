from gzhreader.config import AppConfig, load_config, save_config


def test_legacy_accounts_migrate_to_feeds(tmp_path) -> None:
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

    assert len(cfg.feeds) == 1
    assert cfg.feeds[0].name == "新智元"
    assert cfg.output.raw_archive_dir == "./output/html"
    assert cfg.output.save_raw_html is False
    assert cfg.article_fetch.enabled is True
    assert cfg.article_fetch.browser_channel_order == ["msedge", "chrome"]


def test_save_and_reload_roundtrip(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    cfg = AppConfig()
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.model_dump() == cfg.model_dump()
    assert loaded.wewe_rss.compose_variant == "mysql"
    assert loaded.output.save_raw_html is False
