import pytest
from sqlalchemy import text

from automobile_scraper.storage import Store


def test_legacy_database_is_rejected_before_creating_tables(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'legacy.db'}")
    try:
        with store.engine.begin() as connection:
            connection.execute(text("CREATE TABLE brands (id INTEGER PRIMARY KEY, name TEXT)"))
        with pytest.raises(ValueError, match="dedicated database"):
            store.initialize()
        with store.engine.connect() as connection:
            tables = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            assert {row[0] for row in tables} == {"brands"}
    finally:
        store.close()


def test_updates_clear_removed_specifications_and_replace_photos(store):
    from sqlalchemy import select

    from automobile_scraper.domain import AutomobileData, BrandData, EngineData
    from automobile_scraper.storage import AutomobilePhoto, Engine

    with store.session() as session, session.begin():
        store.save_brand(session, BrandData(name="AC", url="https://x/ac/"))
        data = AutomobileData(
            name="AC Ace 1993-1996",
            brand_name="AC",
            url="https://x/ace.html",
            photos=["https://x/old.jpg"],
            engines=[
                EngineData(
                    other_id=1,
                    name="V6",
                    specs={"Engine Specs": {"Power:": "200 kW", "Torque:": "400 Nm"}},
                )
            ],
        )
        store.save_automobile(session, data)
    with store.session() as session, session.begin():
        data.photos = ["https://x/new.jpg"]
        data.engines[0].specs = {"Engine Specs": {"Power:": "250 kW"}}
        store.save_automobile(session, data)
    with store.session() as session:
        engine = session.scalar(select(Engine))
        assert engine.power_kw == 250
        assert engine.torque is None and engine.torque_nm is None
        assert session.scalars(select(AutomobilePhoto.url)).all() == ["https://x/new.jpg"]
