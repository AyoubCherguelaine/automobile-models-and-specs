"""Bounded retries, shared pacing, robots policy, and immutable raw capture."""

import asyncio
import hashlib
import json
import logging
import random
import time
from urllib.parse import urlencode, urlsplit
from urllib.robotparser import RobotFileParser
from uuid import uuid4

import httpx
from playwright.async_api import Error as BrowserError
from playwright.async_api import async_playwright

from .config import Settings

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


class RetryableFetchError(FetchError):
    pass


class Fetcher:
    def __init__(self, settings: Settings, run_id: str):
        self.settings = settings
        self.raw_dir = settings.data_dir / "raw" / run_id
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.AsyncClient(
            timeout=settings.timeout,
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        )
        self.gate = asyncio.Lock()
        self.robots_gate = asyncio.Lock()
        self.next_request = 0.0
        self.robots: dict[str, RobotFileParser] = {}
        self.playwright = self.browser = self.context = None
        self.request_count = self.retry_count = 0

    async def __aenter__(self):
        try:
            if self.settings.transport == "browser":
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    headless=self.settings.headless
                )
                self.context = await self.browser.new_context(user_agent=self.settings.user_agent)
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self.playwright:
                await self.playwright.stop()
            await self.client.aclose()

    async def pace(self, interval: float):
        async with self.gate:
            await asyncio.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + interval

    async def allowed_interval(self, url: str) -> float:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        expected = urlsplit(self.settings.base_url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != expected.netloc:
            raise FetchError(f"Refusing URL outside configured source: {url}")
        interval = self.settings.request_interval
        if not self.settings.respect_robots:
            return interval
        async with self.robots_gate:
            if origin not in self.robots:
                response = await self.client.get(origin + "/robots.txt")
                policy = RobotFileParser()
                if response.status_code == 404:
                    policy.parse([])
                elif response.status_code in {401, 403}:
                    policy.parse(["User-agent: *", "Disallow: /"])
                else:
                    response.raise_for_status()
                    policy.parse(response.text.splitlines())
                self.robots[origin] = policy
        policy = self.robots[origin]
        if not policy.can_fetch(self.settings.user_agent, url):
            raise FetchError(f"robots.txt disallows {url}")
        return max(interval, policy.crawl_delay(self.settings.user_agent) or 0)

    @staticmethod
    def check_status(status: int, url: str):
        if status == 429 or status >= 500:
            raise RetryableFetchError(f"HTTP {status}: {url}")
        if status >= 400:
            raise FetchError(f"HTTP {status}: {url}")

    def capture(self, url: str, form: dict | None, html: str, status: int):
        key = hashlib.sha256(json.dumps([url, form, html], sort_keys=True).encode()).hexdigest()
        path = self.raw_dir / f"{key}-{uuid4().hex}.html"
        path.write_text(html, encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "url": url,
                    "method": "POST" if form is not None else "GET",
                    "form": form,
                    "status": status,
                    "captured_at": time.time(),
                    "sha256": hashlib.sha256(html.encode()).hexdigest(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    async def request(self, url: str, form: dict[str, str] | None = None) -> str:
        for attempt in range(self.settings.attempts):
            try:
                interval = await self.allowed_interval(url)
                await self.pace(interval)
                self.request_count += 1
                if self.settings.transport == "http":
                    response = (
                        await self.client.post(url, data=form)
                        if form is not None
                        else await self.client.get(url)
                    )
                    html, status = response.text, response.status_code
                else:
                    page = await self.context.new_page()
                    try:
                        if form is not None:

                            async def post_route(route):
                                await route.continue_(
                                    method="POST",
                                    post_data=urlencode(form),
                                    headers={
                                        **route.request.headers,
                                        "content-type": "application/x-www-form-urlencoded",
                                    },
                                )

                            await page.route(url, post_route, times=1)
                        response = await page.goto(
                            url, wait_until="load", timeout=self.settings.timeout * 1000
                        )
                        if response is None:
                            raise RetryableFetchError(f"No navigation response: {url}")
                        html, status = await page.content(), response.status
                    finally:
                        await page.close()
                self.capture(url, form, html, status)
                self.check_status(status, url)
                return html
            except (httpx.HTTPError, BrowserError, RetryableFetchError) as exc:
                # Authentication/authorization failures are never worked around.
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    if exc.response.status_code != 429:
                        raise FetchError(
                            f"HTTP {exc.response.status_code} fetching source policy"
                        ) from exc
                if attempt + 1 == self.settings.attempts:
                    raise FetchError(f"Request failed after {attempt + 1} attempts: {url}") from exc
                self.retry_count += 1
                log.warning("request_retry", extra={"url": url, "attempt": attempt + 1})
                await asyncio.sleep(min(30, 2**attempt) + random.random())
        raise AssertionError("Unreachable")
