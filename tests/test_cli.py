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
