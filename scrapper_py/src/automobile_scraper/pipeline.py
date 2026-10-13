import asyncio
import logging
from uuid import uuid4

from sqlalchemy import select

from .config import Settings
from .domain import url_key
from .fetching import Fetcher
from .parsers import (
    parse_automobile,
    parse_brand_ids,
    parse_brands,
    parse_model_urls,
    parse_press_release,
    press_release_id,
)
from .storage import RUN_COUNT_COLUMNS, Automobile, Run, Store, WorkItem, now

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store

    async def brands(self, fetcher) -> int:
        data = parse_brands(
            await fetcher.request(self.settings.base_url + "/cars/"), self.settings.base_url
        )
        with self.store.session() as session, session.begin():
            for brand in data:
                self.store.save_brand(session, brand)
        return len(data)

    async def discover(self, fetcher) -> list[str]:
        url = self.settings.base_url + "/carfinder/"
        ids = parse_brand_ids(await fetcher.request(url))
        return parse_model_urls(
            await fetcher.request(url, {"n[brand]": ",".join(ids), "n[submitted]": "1"}),
            self.settings.base_url,
        )

    async def run(self, mode: str = "new", limit: int | None = None, fetcher=None) -> dict:
        run_id = str(uuid4())
        summary = dict(
            run_id=run_id,
            mode=mode,
            brands=0,
            discovered=0,
            attempted=0,
            succeeded=0,
            skipped=0,
            failed=0,
            requests=0,
            retried=0,
        )
        with self.store.session() as session, session.begin():
            session.add(Run(id=run_id, mode=mode))
        try:
            if fetcher is None:
                async with Fetcher(self.settings, run_id) as transport:
                    await self._run(transport, run_id, mode, limit, summary)
                    summary.update(requests=transport.request_count, retried=transport.retry_count)
            else:
                await self._run(fetcher, run_id, mode, limit, summary)
            status = "partial" if summary["failed"] else "completed"
        except BaseException:
            status = "interrupted"
            raise
        finally:
            with self.store.session() as session, session.begin():
                row = session.get(Run, run_id)
                row.status, row.finished_at = status, now()
                for column in RUN_COUNT_COLUMNS:
                    setattr(row, column, summary[column])
        return summary

    async def _run(self, fetcher, run_id, mode, limit, summary):
        if mode in {"new", "all", "brands"}:
            summary["brands"] = await self.brands(fetcher)
        if mode == "brands":
            return
        if mode in {"new", "all"}:
            urls = await self.discover(fetcher)
            summary["discovered"] = len(urls)
            with self.store.session() as session, session.begin():
                known = set(session.scalars(select(Automobile.url_hash)))
                for url in urls:
                    key = url_key(url)
                    if mode == "new" and key in known:
                        summary["skipped"] += 1
                        continue
                    work = session.get(WorkItem, key)
                    if work is None:
                        session.add(WorkItem(url_hash=key, url=url, status="queued"))
                    else:
                        work.status, work.last_error = "queued", None
        elif mode == "update":
            with self.store.session() as session, session.begin():
                for row in session.scalars(select(Automobile)):
                    work = session.get(WorkItem, row.url_hash)
                    if work is None:
                        session.add(WorkItem(url_hash=row.url_hash, url=row.url, status="queued"))
                    else:
                        work.status, work.last_error = "queued", None
        statuses = ["failed"] if mode == "retry-failed" else ["queued", "started", "failed"]
        with self.store.session() as session:
            query = select(WorkItem.url).where(WorkItem.status.in_(statuses)).order_by(WorkItem.url)
            if mode == "update":
                query = query.where(WorkItem.url_hash.in_(select(Automobile.url_hash)))
            elif mode == "new":
                query = query.where(WorkItem.url_hash.not_in(select(Automobile.url_hash)))
            if limit:
                query = query.limit(limit)
            urls = list(session.scalars(query))
        # Fixed number of workers: do not create thousands of browser tasks at once.
        queue = asyncio.Queue()
        for url in urls:
            queue.put_nowait(url)

        async def worker():
            while not queue.empty():
                url = queue.get_nowait()
                key = url_key(url)
                summary["attempted"] += 1
                with self.store.session() as session, session.begin():
                    work = session.get(WorkItem, key)
                    work.status, work.run_id = "started", run_id
                    work.attempts += 1
                try:
                    html = await fetcher.request(url)
                    data = parse_automobile(html, url)
                    release_id = press_release_id(html)
                    if release_id:
                        data.press_release = parse_press_release(
                            await fetcher.request(
                                self.settings.base_url + "/rhh.php?k=pr_cars&i=" + release_id
                            )
                        )
                    with self.store.session() as session, session.begin():
                        self.store.save_automobile(session, data)
                        work = session.get(WorkItem, key)
                        work.status, work.last_error = "completed", None
                    summary["succeeded"] += 1
                    log.info("model_completed", extra={"url": url, "run_id": run_id})
                except Exception as exc:
                    with self.store.session() as session, session.begin():
                        work = session.get(WorkItem, key)
                        work.status, work.last_error = "failed", f"{type(exc).__name__}: {exc}"
                    summary["failed"] += 1
                    log.error(
                        "model_failed",
                        extra={
                            "url": url,
                            "run_id": run_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                finally:
                    queue.task_done()

        tasks = [asyncio.create_task(worker()) for _ in range(self.settings.concurrency)]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
