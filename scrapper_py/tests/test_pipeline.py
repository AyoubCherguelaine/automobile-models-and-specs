from conftest import BRANDS, DETAIL, FINDER, RESULTS
from sqlalchemy import select

from automobile_scraper.config import Settings
from automobile_scraper.domain import url_key
from automobile_scraper.pipeline import Pipeline
from automobile_scraper.storage import Automobile, Engine, WorkItem

BASE = "https://www.autoevolution.com"
URL = BASE + "/cars/ac-ace.html"


class FakeFetcher:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def request(self, url, form=None):
        self.calls.append((url, form))
        if url.endswith("/cars/"):
            return BRANDS
        if url.endswith("/carfinder/"):
            if form:
                assert form == {"n[brand]": "12", "n[submitted]": "1"}
                return RESULTS
            return FINDER
        if "/rhh.php" in url:
            return '<div class="content"><p>Release</p></div>'
        if self.fail:
            raise RuntimeError("temporary failure")
        return DETAIL


async def test_resume_idempotency_and_update(store, tmp_path):
    pipeline = Pipeline(Settings(data_dir=tmp_path, _env_file=None), store)
    failed = await pipeline.run(fetcher=FakeFetcher(fail=True))
    assert failed["failed"] == 1
    assert store.counts()["automobiles"] == 0
    resumed = await pipeline.run("resume", fetcher=FakeFetcher())
    assert resumed["succeeded"] == 1
    with store.session() as session:
        assert session.get(WorkItem, url_key(URL)).status == "completed"
        assert session.scalar(select(Automobile)).press_release == "<p>Release</p>"
    repeated = await pipeline.run(fetcher=FakeFetcher())
    assert repeated["skipped"] == 1 and repeated["attempted"] == 0
    fetcher = FakeFetcher()
    updated = await pipeline.run("update", fetcher=fetcher)
    assert updated["succeeded"] == 1
    assert not any("carfinder" in url or url.endswith("/cars/") for url, _ in fetcher.calls)
    assert store.validate() == {"brands": 1, "automobiles": 1, "engines": 1}


async def test_interrupted_work_and_limit(store, tmp_path):
    pipeline = Pipeline(Settings(data_dir=tmp_path, _env_file=None), store)
    await pipeline.run(fetcher=FakeFetcher())
    with store.session() as session, session.begin():
        session.get(WorkItem, url_key(URL)).status = "started"
        session.add(WorkItem(url_hash=url_key(URL + "?two"), url=URL + "?two", status="queued"))
    result = await pipeline.run("resume", limit=1, fetcher=FakeFetcher())
    assert result["attempted"] == 1
    with store.session() as session:
        assert (
            len(session.scalars(select(WorkItem).where(WorkItem.status != "completed")).all()) == 1
        )


async def test_failed_update_preserves_saved_model(store, tmp_path):
    pipeline = Pipeline(Settings(data_dir=tmp_path, _env_file=None), store)
    await pipeline.run(fetcher=FakeFetcher())
    with store.session() as session:
        original = session.scalar(select(Engine)).id
    assert (await pipeline.run("update", fetcher=FakeFetcher(fail=True)))["failed"] == 1
    with store.session() as session:
        assert session.scalar(select(Engine)).id == original


async def test_storage_failure_rolls_back_data_and_checkpoint(store, tmp_path, monkeypatch):
    pipeline = Pipeline(Settings(data_dir=tmp_path, _env_file=None), store)
    save = store.save_automobile

    def fail_after_writing(session, data):
        save(session, data)
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(store, "save_automobile", fail_after_writing)
    result = await pipeline.run(fetcher=FakeFetcher())
    assert result["failed"] == 1
    assert store.counts() == {"brands": 1, "automobiles": 0, "engines": 0}
    with store.session() as session:
        assert session.get(WorkItem, url_key(URL)).status == "failed"


async def test_new_mode_does_not_retry_existing_model_updates(store, tmp_path):
    pipeline = Pipeline(Settings(data_dir=tmp_path, _env_file=None), store)
    await pipeline.run(fetcher=FakeFetcher())
    await pipeline.run("update", fetcher=FakeFetcher(fail=True))
    result = await pipeline.run("new", fetcher=FakeFetcher())
    assert result["attempted"] == 0
