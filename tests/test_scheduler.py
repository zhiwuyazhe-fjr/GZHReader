from gzhreader.config import AppConfig


def test_schedule_config_default_time() -> None:
    cfg = AppConfig()
    assert cfg.schedule.daily_time == "21:30"
