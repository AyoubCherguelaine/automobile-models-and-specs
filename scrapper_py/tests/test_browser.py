"""Real Chromium smoke test against a local server; opt in with RUN_BROWSER_TESTS=1."""

import asyncio
import os

import pytest

from automobile_scraper.config import Settings
from automobile_scraper.fetching import Fetcher

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="requires installed Chromium"
)


async def test_browser_post_retry_preserves_form(tmp_path, monkeypatch):
    calls = []

    async def handler(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode().split("\r\n")
            method, path, _ = lines[0].split()
            headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
            length = next(
                (value for key, value in headers.items() if key.lower() == "content-length"), "0"
            )
            body = await reader.readexactly(int(length))
            if path == "/finder/":
                calls.append((method, body))
            status = "503 Service Unavailable" if len(calls) == 1 else "200 OK"
            html = b"<html><body>Local browser test</body></html>"
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: text/html\r\n"
                f"Content-Length: {len(html)}\r\nConnection: close\r\n\r\n".encode()
                + html
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    settings = Settings(
        base_url=f"http://127.0.0.1:{port}",
        data_dir=tmp_path,
        transport="browser",
        respect_robots=False,
        request_interval=0,
        attempts=2,
        _env_file=None,
    )
    try:
        async with Fetcher(settings, "browser-test") as fetcher:
            result = await fetcher.request(settings.base_url + "/finder/", {"n[brand]": "12"})
            assert "Local browser test" in result
            assert fetcher.retry_count == 1
    finally:
        server.close()
        await server.wait_closed()
    assert calls == [("POST", b"n%5Bbrand%5D=12")] * 2
