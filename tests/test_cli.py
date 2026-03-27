from gzhreader import cli


class DummyResponse:
    status_code = 200


def test_wait_for_health_and_open_browser_opens_after_success(monkeypatch) -> None:
    calls: list[str] = []
    opened: list[str] = []

    def fake_get(url: str, timeout: float):
        calls.append(url)
        return DummyResponse()

    monkeypatch.setattr(cli.httpx, "get", fake_get)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)

    ok = cli._wait_for_health_and_open_browser(
        "http://127.0.0.1:8765",
        timeout_seconds=0.01,
        interval_seconds=0.001,
    )

    assert ok is True
    assert calls == ["http://127.0.0.1:8765/healthz"]
    assert opened == ["http://127.0.0.1:8765"]


def test_run_gui_server_disables_uvicorn_default_log_config(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli,
        "_bootstrap_config",
        lambda config, force: type("Cfg", (), {"output": type("Output", (), {"log_level": "INFO"})()})(),
    )
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    monkeypatch.setattr(cli, "create_web_app", lambda config_path=None: object())
    monkeypatch.setattr(cli.typer, "echo", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_resolve_gui_port", lambda host, port: (port, "new"))

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.run_gui_server(config=tmp_path / "config.yaml", host="127.0.0.1", port=8765, open_browser=False)

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["log_level"] == "info"
    assert captured["log_config"] is None


def test_resolve_gui_port_returns_existing_when_healthz_matches(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_can_bind_port", lambda host, port: False)
    monkeypatch.setattr(cli, "_is_existing_gzhreader_gui", lambda base_url: True)

    port, mode = cli._resolve_gui_port("127.0.0.1", 8765)

    assert port == 8765
    assert mode == "existing"


def test_resolve_gui_port_falls_back_to_next_available_port(monkeypatch) -> None:
    bindable_ports = {8767}
    monkeypatch.setattr(cli, "_can_bind_port", lambda host, port: port in bindable_ports)
    monkeypatch.setattr(cli, "_is_existing_gzhreader_gui", lambda base_url: False)

    port, mode = cli._resolve_gui_port("127.0.0.1", 8765)

    assert port == 8767
    assert mode == "fallback"
