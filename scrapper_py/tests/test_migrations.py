import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from automobile_scraper.domain import url_key
from automobile_scraper.preprocessing import UnknownSpecification
from automobile_scraper.storage import Base, Store

DATE = "2024-01-01 00:00:00"
SPEC = {
    "Engine Specs": {
        "Power:": "200 kW",
        "Torque:": "407 Nm",
        "Cylinders:": "V6",
        "Fuel:": "Gasoline",
        "Displacement:": "2998 cm3",
    },
    "Transmission Specs": {"Drive Type:": "Rear Wheel Drive", "Gearbox:": "6-Speed Manual"},
}


def legacy_store(path, specs=SPEC):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE brands (id INTEGER PRIMARY KEY, url_hash VARCHAR(64) UNIQUE,
                url TEXT, name TEXT, logo TEXT, deleted_at DATETIME,
                created_at DATETIME, updated_at DATETIME);
            CREATE TABLE automobiles (id INTEGER PRIMARY KEY, url_hash VARCHAR(64) UNIQUE,
                url TEXT, brand_id INTEGER REFERENCES brands(id), name TEXT,
                description TEXT, press_release TEXT, photos JSON,
                created_at DATETIME, updated_at DATETIME);
            CREATE TABLE engines (id INTEGER PRIMARY KEY, other_id INTEGER,
                automobile_id INTEGER REFERENCES automobiles(id), name TEXT, specs JSON NOT NULL,
                created_at DATETIME, updated_at DATETIME, UNIQUE(automobile_id, other_id));
            CREATE TABLE scrape_runs (id VARCHAR(36) PRIMARY KEY, mode TEXT, status TEXT,
                started_at DATETIME, finished_at DATETIME, summary JSON);
        """)
        db.execute(
            "INSERT INTO brands VALUES (7,?,?,'AC',NULL,NULL,?,?)",
            (url_key("https://x/ac/"), "https://x/ac/", DATE, DATE),
        )
        db.execute(
            "INSERT INTO automobiles VALUES (9,?,?,7,?,NULL,NULL,?,?,?)",
            (
                url_key("https://x/ac-1998.html"),
                "https://x/ac-1998.html",
                "AC Ace 1998-2000 Photos",
                json.dumps(["https://x/one.jpg", "https://x/two.jpg"]),
                DATE,
                DATE,
            ),
        )
        db.execute(
            "INSERT INTO engines VALUES (11,123,9,'V6',?,?,?)", (json.dumps(specs), DATE, DATE)
        )
        db.execute(
            "INSERT INTO scrape_runs VALUES ('old-run','new','completed',?,NULL,?)",
            (DATE, json.dumps({"attempted": 3, "succeeded": 2, "failed": 1})),
        )
    store = Store(f"sqlite:///{path}")
    Base.metadata.create_all(store.engine)
    return store


def test_migration_preserves_data_ids_timestamps_and_history(tmp_path):
    store = legacy_store(tmp_path / "legacy.db")
    try:
        result = store.preprocess()
        assert result["migrated"] is True and Path(result["backup"]).exists()
        with sqlite3.connect(result["backup"]) as backup:
            assert "specs" in {row[1] for row in backup.execute("PRAGMA table_info(engines)")}
        with store.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM vehicle_specs")).mappings().one()
            assert row["engine_id"] == 11 and row["automobile_id"] == 9
            assert row["country"] == "United Kingdom"
            assert row["year"] == 1998 and row["year_end"] == 2000
            assert row["cylinders"] == 6 and row["engine_type"] == "V6"
            assert row["power_kw"] == 200 and row["torque_nm"] == 407
            assert row["T_gearbox"] == "6-Speed Manual"
            assert connection.execute(
                text("SELECT attempted,succeeded,failed FROM scrape_runs")
            ).one() == (3, 2, 1)
            assert connection.execute(
                text("SELECT url FROM automobile_photos ORDER BY position")
            ).scalars().all() == ["https://x/one.jpg", "https://x/two.jpg"]
            for table in ("brands", "automobiles", "engines"):
                assert connection.execute(text(f"SELECT updated_at FROM {table}")).scalar() == DATE
        for table in inspect(store.engine).get_table_names():
            assert not any(
                str(column["type"]).upper() == "JSON"
                for column in inspect(store.engine).get_columns(table)
            )
        assert store.preprocess()["migrated"] is False
        assert store.validate() == {"brands": 1, "automobiles": 1, "engines": 1}
        with store.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM automobile_photos")).scalar() == 2
    finally:
        store.close()


def test_failed_migration_rolls_back_ddl_and_data(tmp_path):
    store = legacy_store(tmp_path / "broken.db", {"Engine Specs": {"New Field:": "secret"}})
    try:
        with pytest.raises(UnknownSpecification):
            store.preprocess()
        inspector = inspect(store.engine)
        assert "specs" in {column["name"] for column in inspector.get_columns("engines")}
        assert "power_kw" not in {column["name"] for column in inspector.get_columns("engines")}
        assert "country" not in {column["name"] for column in inspector.get_columns("brands")}
        with store.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM automobile_photos")).scalar() == 0
            assert connection.execute(text("SELECT updated_at FROM automobiles")).scalar() == DATE
    finally:
        store.close()
