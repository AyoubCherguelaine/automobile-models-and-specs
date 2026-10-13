"""Stream the repository's Laravel JSON archive into the Python schema."""

import json
import sqlite3
import tempfile
from pathlib import Path
from zipfile import ZipFile

import ijson
from sqlalchemy import select

from .domain import AutomobileData, BrandData, EngineData
from .spec_fields import ENGINE_COLUMNS
from .storage import Brand, Store
from .vehicle_information import VEHICLE_COLUMNS


def decode(value, default):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def import_archive(store: Store, archive: Path) -> dict:
    counts = dict(brands=0, automobiles=0, engines=0)
    # Disk-backed engine grouping avoids loading the 100+ MB dataset in memory.
    with ZipFile(archive) as zipped, tempfile.TemporaryDirectory() as directory:
        if not {"brands.json", "automobiles.json", "engines.json"}.issubset(zipped.namelist()):
            raise ValueError("Archive must contain brands.json, automobiles.json, engines.json")
        staged = sqlite3.connect(Path(directory) / "engines.db")
        try:
            staged.execute("CREATE TABLE engines (automobile_id INTEGER, payload TEXT)")
            with zipped.open("engines.json") as source:
                for item in ijson.items(source, "item", use_float=True):
                    engine = EngineData(
                        other_id=item["other_id"],
                        name=item["name"],
                        specs=decode(item.get("specs"), {}),
                        columns={key: item[key] for key in ENGINE_COLUMNS if key in item},
                    )
                    staged.execute(
                        "INSERT INTO engines VALUES (?, ?)",
                        (item["automobile_id"], engine.model_dump_json()),
                    )
            staged.execute("CREATE INDEX engine_parent ON engines(automobile_id)")
            staged.execute(
                "CREATE TABLE photos (automobile_id INTEGER, position INTEGER, url TEXT)"
            )
            if "automobile_photos.json" in zipped.namelist():
                with zipped.open("automobile_photos.json") as source:
                    for item in ijson.items(source, "item", use_float=True):
                        staged.execute(
                            "INSERT INTO photos VALUES (?, ?, ?)",
                            (item["automobile_id"], item["position"], item["url"]),
                        )
                staged.execute("CREATE INDEX photo_parent ON photos(automobile_id)")
            staged.commit()
            brands = {}
            # Whole import is transactional; invalid references roll everything back.
            with store.session() as session, session.begin():
                with zipped.open("brands.json") as source:
                    for item in ijson.items(source, "item", use_float=True):
                        row = store.save_brand(
                            session,
                            BrandData(
                                url=item["url"],
                                name=item["name"],
                                logo=item.get("logo"),
                                country=item.get("country"),
                            ),
                        )
                        brands[item["id"]] = row.id
                        counts["brands"] += 1
                with zipped.open("automobiles.json") as source:
                    model_ids = set()
                    for item in ijson.items(source, "item", use_float=True):
                        brand_id = brands[item["brand_id"]]
                        engines = [
                            EngineData.model_validate_json(row[0])
                            for row in staged.execute(
                                "SELECT payload FROM engines WHERE automobile_id=?", (item["id"],)
                            )
                        ]
                        name = session.scalar(select(Brand.name).where(Brand.id == brand_id))
                        data = AutomobileData(
                            url=item["url"],
                            brand_name=name,
                            name=item["name"],
                            description=item.get("description"),
                            press_release=item.get("press_release"),
                            photos=(
                                [
                                    row[0]
                                    for row in staged.execute(
                                        "SELECT url FROM photos "
                                        "WHERE automobile_id=? ORDER BY position",
                                        (item["id"],),
                                    )
                                ]
                                if "automobile_photos.json" in zipped.namelist()
                                else decode(item.get("photos"), None)
                            ),
                            engines=engines,
                            information={key: item[key] for key in VEHICLE_COLUMNS if key in item},
                        )
                        store.save_automobile(session, data, brand_id)
                        model_ids.add(item["id"])
                        counts["automobiles"] += 1
                        counts["engines"] += len(engines)
                    parents = {
                        row[0]
                        for row in staged.execute("SELECT DISTINCT automobile_id FROM engines")
                    }
                    parents.update(
                        row[0]
                        for row in staged.execute("SELECT DISTINCT automobile_id FROM photos")
                    )
                    if parents - model_ids:
                        raise ValueError("Archive contains orphan engines or photos")
        finally:
            staged.close()
    return counts
