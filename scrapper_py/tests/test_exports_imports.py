import csv
import io
import json
import sqlite3
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest
from conftest import BRANDS, DETAIL

from automobile_scraper.exporting import export_data
from automobile_scraper.importing import import_archive
from automobile_scraper.parsers import parse_automobile, parse_brands
from automobile_scraper.storage import Store

BASE = "https://www.autoevolution.com"


def populate(store):
    with store.session() as session, session.begin():
        store.save_brand(session, parse_brands(BRANDS, BASE)[0])
        car = parse_automobile(DETAIL, BASE + "/cars/ac-ace.html")
        car.information.update(
            {
                "body_style": "Wagon",
                "body_type": "Wagon",
                "seats": 5,
                "supports_android_auto": True,
                "vehicle_configuration": "5dr wagon",
            }
        )
        car.engines[0].specs.setdefault("Engine Specs", {})["Fuel:"] = "Gasoline"
        store.save_automobile(session, car)


def test_round_trip_all_export_formats(store, tmp_path):
    populate(store)
    paths = export_data(store, tmp_path / "exports", ["json", "csv", "xml", "sql"])
    with ZipFile(paths[0]) as archive:
        cars = json.loads(archive.read("automobiles.json"))
        assert "photos" not in cars[0]
        assert cars[0]["seats"] == 5 and cars[0]["supports_android_auto"] is True
        assert cars[0]["vehicle_configuration"] == "5dr wagon"
        assert json.loads(archive.read("automobile_photos.json"))[0]["url"] == BASE + "/photo.jpg"
        engine = json.loads(archive.read("engines.json"))[0]
        assert "specs" not in engine
        assert engine["powertrain_type"] == "Combustion"
        assert engine["fuel_type"] == "Gasoline"
        assert engine["power_kw"] == 200
        assert engine["power"] == "200 kW\n300 HP"
    with ZipFile(paths[1]) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("automobiles.csv").decode())))
        assert rows[0]["name"] == "AC Ace & Specs"
        assert "description" in rows[0] and "engine_id" not in rows[0]
    with ZipFile(paths[2]) as archive:
        assert (
            ET.fromstring(archive.read("automobiles.xml")).find("automobile/name").text
            == "AC Ace & Specs"
        )
    destination = Store(f"sqlite:///{tmp_path / 'destination.db'}")
    destination.initialize()
    try:
        assert import_archive(destination, paths[0]) == {
            "brands": 1,
            "automobiles": 1,
            "engines": 1,
        }
        import_archive(destination, paths[0])
        assert destination.validate() == {"brands": 1, "automobiles": 1, "engines": 1}
        with destination.engine.connect() as connection:
            from sqlalchemy import text

            assert connection.execute(text("SELECT count(*) FROM automobile_photos")).scalar() == 1
            assert connection.execute(text("SELECT power_kw FROM engines")).scalar() == 200
            assert connection.execute(
                text(
                    "SELECT body_type,seats,supports_android_auto,vehicle_configuration,"
                    "powertrain_type,fuel_type FROM vehicle_specs"
                )
            ).one() == ("Wagon", 5, True, "5dr wagon", "Combustion", "Gasoline")
    finally:
        destination.close()
    sql_store = Store(f"sqlite:///{tmp_path / 'sql.db'}")
    sql_store.initialize()
    sql_store.close()
    with sqlite3.connect(tmp_path / "sql.db") as connection, ZipFile(paths[3]) as archive:
        for table in ("brands", "automobiles", "engines", "automobile_photos"):
            connection.executescript(archive.read(table + ".sql").decode())
        assert connection.execute("SELECT count(*) FROM engines").fetchone()[0] == 1


def test_orphan_import_rolls_back(store, tmp_path):
    archive_path = tmp_path / "broken.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("brands.json", json.dumps([{"id": 1, "url": BASE + "/ac/", "name": "AC"}]))
        archive.writestr("automobiles.json", "[]")
        archive.writestr(
            "engines.json",
            json.dumps([{"automobile_id": 999, "other_id": 1, "name": "V8", "specs": {}}]),
        )
    with pytest.raises(ValueError, match="orphan"):
        import_archive(store, archive_path)
    assert store.counts() == {"brands": 0, "automobiles": 0, "engines": 0}


def test_original_nested_archive_is_preprocessed_on_import(store, tmp_path):
    archive_path = tmp_path / "original.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("brands.json", json.dumps([{"id": 1, "url": BASE + "/ac/", "name": "AC"}]))
        archive.writestr(
            "automobiles.json",
            json.dumps(
                [
                    {
                        "id": 2,
                        "brand_id": 1,
                        "url": BASE + "/cars/ac-ace-1993.html",
                        "name": "AC Ace 1993-1996",
                        "photos": [BASE + "/photo.jpg"],
                    }
                ]
            ),
        )
        archive.writestr(
            "engines.json",
            json.dumps(
                [
                    {
                        "id": 3,
                        "automobile_id": 2,
                        "other_id": 1234,
                        "name": "V6",
                        "specs": {"Engine Specs": {"Cylinders:": "V6", "Power:": "200 kW"}},
                    }
                ]
            ),
        )
    import_archive(store, archive_path)
    from sqlalchemy import text

    with store.engine.connect() as connection:
        assert connection.execute(
            text("SELECT year,year_end,country,engine_type,cylinders,power_kw FROM vehicle_specs")
        ).one() == (1993, 1996, "United Kingdom", "V6", 6, 200)
        assert (
            connection.execute(text("SELECT url FROM automobile_photos")).scalar()
            == BASE + "/photo.jpg"
        )
