import asyncio
import importlib

import pytest


def _load_scraper(monkeypatch, *, login_is_edge: bool, running_in_docker: bool):
    monkeypatch.setenv("LOGIN_IS_EDGE", "true" if login_is_edge else "false")
    monkeypatch.setenv("RUNNING_IN_DOCKER", "true" if running_in_docker else "false")

    import src.config as config_module
    import src.scraper as scraper_module

    importlib.reload(config_module)
    reloaded_scraper = importlib.reload(scraper_module)
    reloaded_scraper.EDGE_DOCKER_WARNING_PRINTED = False
    return reloaded_scraper


def test_resolve_browser_channel_uses_chromium_in_docker_even_when_edge_requested(monkeypatch, capsys):
    scraper = _load_scraper(monkeypatch, login_is_edge=True, running_in_docker=True)

    assert scraper._resolve_browser_channel() == "chromium"
    assert "Docker 镜像未内置 Edge" in capsys.readouterr().out


def test_resolve_browser_channel_uses_msedge_locally_when_requested(monkeypatch):
    scraper = _load_scraper(monkeypatch, login_is_edge=True, running_in_docker=False)

    assert scraper._resolve_browser_channel() == "msedge"


def test_chromium_launch_args_disable_crashpad_and_pin_dump_dir():
    from src.scraper import build_chromium_launch_args

    args = build_chromium_launch_args("/tmp/chromium-crashpad")

    assert "--disable-breakpad" in args
    assert "--disable-crash-reporter" in args
    assert "--crash-dumps-dir=/tmp/chromium-crashpad" in args
    feature_flags = [arg for arg in args if arg.startswith("--disable-features=")]
    assert feature_flags == ["--disable-features=IsolateOrigins,site-per-process,Crashpad"]


def test_initial_page_timeout_defaults_to_sixty_seconds(monkeypatch):
    monkeypatch.delenv("GOOFISH_INITIAL_PAGE_TIMEOUT_MS", raising=False)

    from src.scraper import _get_initial_page_timeout_ms

    assert _get_initial_page_timeout_ms() == 60000


def test_browser_closure_raises_a_task_failure():
    from src.scraper import BrowserClosedError, raise_browser_closed_error

    with pytest.raises(BrowserClosedError, match="浏览器意外关闭"):
        raise_browser_closed_error(RuntimeError("Target page, context or browser has been closed"))


def test_access_denied_page_is_reported_as_risk_control():
    from src.scraper import RiskControlError, raise_if_goofish_access_denied

    class Body:
        async def inner_text(self, timeout):
            assert timeout == 2000
            return "非法访问\n为了保障您的体验，请使用正常浏览器访问闲鱼~"

    class Page:
        def locator(self, selector):
            assert selector == "body"
            return Body()

    with pytest.raises(RiskControlError, match="闲鱼拒绝了自动化浏览器访问"):
        asyncio.run(raise_if_goofish_access_denied(Page()))


def test_ordinary_timeout_is_not_mislabeled_as_access_denial():
    from src.scraper import raise_if_goofish_access_denied

    class Body:
        async def inner_text(self, timeout):
            return "闲鱼搜索结果"

    class Page:
        def locator(self, selector):
            return Body()

    asyncio.run(raise_if_goofish_access_denied(Page()))
