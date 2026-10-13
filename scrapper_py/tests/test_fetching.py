import httpx
import pytest

from automobile_scraper.config import Settings
from automobile_scraper.fetching import Fetcher, FetchError


async def test_post_retry_preserves_body_and_raw_capture(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.method, request.content))
        return httpx.Response(503 if len(calls) == 1 else 200, text="<html>OK</html>")

    async def no_sleep(_):
        pass

    monkeypatch.setattr("automobile_scraper.fetching.asyncio.sleep", no_sleep)
    settings = Settings(
        transport="http",
        data_dir=tmp_path,
        respect_robots=False,
        request_interval=0,
        _env_file=None,
    )
    async with Fetcher(settings, "test") as fetcher:
        await fetcher.client.aclose()
        fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert (
            await fetcher.request(settings.base_url + "/carfinder/", {"n[brand]": "12"})
            == "<html>OK</html>"
        )
        assert fetcher.retry_count == 1
    assert calls == [("POST", b"n%5Bbrand%5D=12")] * 2
    assert len(list((tmp_path / "raw/test").glob("*.html"))) == 2


async def test_retry_is_bounded(tmp_path, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr("automobile_scraper.fetching.asyncio.sleep", no_sleep)
    settings = Settings(
        transport="http",
        data_dir=tmp_path,
        respect_robots=False,
        request_interval=0,
        attempts=2,
        _env_file=None,
    )
    async with Fetcher(settings, "test") as fetcher:
        await fetcher.client.aclose()
        fetcher.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, text="server failure")
            )
        )
        with pytest.raises(FetchError, match="after 2 attempts"):
            await fetcher.request(settings.base_url + "/cars/")
        assert fetcher.request_count == 2


async def test_robots_denial_makes_no_model_request(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /cars/\n")

    settings = Settings(transport="http", data_dir=tmp_path, _env_file=None)
    async with Fetcher(settings, "test") as fetcher:
        await fetcher.client.aclose()
        fetcher.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(FetchError, match="disallows"):
            await fetcher.request(settings.base_url + "/cars/")
    assert calls == [settings.base_url + "/robots.txt"]
