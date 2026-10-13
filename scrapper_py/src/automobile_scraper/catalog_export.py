"""Export one flat cross-source model catalog without merging uncertain identities."""

import csv
import json
import sqlite3
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from .storage import Automobile, Brand, Engine

COLUMNS = (
    "source",
    "source_id",
    "make_name",
    "model_name",
    "vehicle_category",
    "vehicle_type",
    "year",
    "year_end",
    "source_url",
    "detail_level",
)


def catalog_rows(store, catalog_directory, motorcycle_directory=None, manufacturer_directory=None):
    with store.session() as session:
        has_specs = select(Engine.id).where(Engine.automobile_id == Automobile.id).exists()
        query = select(Automobile, Brand.name, has_specs).join(
            Brand, Automobile.brand_id == Brand.id
        )
        for row, brand, has_specs in session.execute(query).yield_per(500):
            yield {
                "source": "autoevolution",
                "source_id": row.url_hash,
                "make_name": brand,
                "model_name": row.model_name or row.name,
                "vehicle_category": row.vehicle_category,
                "vehicle_type": row.vehicle_type,
                "year": row.year,
                "year_end": row.year_end,
                "source_url": row.url,
                "detail_level": "model_and_variant_specs" if has_specs else "model_listing",
            }
    path = catalog_directory / "vehicles.db"
    if path.is_file():
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            for row in connection.execute("SELECT * FROM vehicles ORDER BY make_id,model_id"):
                yield {
                    "source": row["source"],
                    "source_id": f"{row['make_id']}:{row['model_id']}:{row['vehicle_type_id']}",
                    "make_name": row["make_name"],
                    "model_name": row["model_name"],
                    "vehicle_category": row["vehicle_category"],
                    "vehicle_type": row["vehicle_type"],
                    "year": None,
                    "year_end": None,
                    "source_url": row["source_url"],
                    "detail_level": row["detail_level"],
                }

    if motorcycle_directory and (motorcycle_directory / "vehicles.db").is_file():
        path = motorcycle_directory / "vehicles.db"
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            for row in connection.execute("SELECT * FROM vehicles ORDER BY url_hash"):
                yield {
                    "source": "autoevolution_moto",
                    "source_id": row["url_hash"],
                    "make_name": row["make_name"],
                    "model_name": row["model_name"],
                    "vehicle_category": row["vehicle_category"],
                    "vehicle_type": row["vehicle_type"],
                    "year": row["year"],
                    "year_end": row["year_end"],
                    "source_url": row["url"],
                    "detail_level": "model_and_variant_specs",
                }

    if manufacturer_directory and (manufacturer_directory / "specifications.db").is_file():
        import hashlib

        path = manufacturer_directory / "specifications.db"
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            for row in connection.execute("SELECT * FROM models ORDER BY source_url"):
                yield {
                    "source": row["source"],
                    "source_id": hashlib.sha256(row["source_url"].encode()).hexdigest(),
                    "make_name": row["make_name"],
                    "model_name": row["model_name"],
                    "vehicle_category": row["vehicle_category"],
                    "vehicle_type": row["vehicle_type"],
                    "year": None,
                    "year_end": None,
                    "source_url": row["source_url"],
                    "detail_level": "specification_tables",
                }


def export_catalog(
    store,
    catalog_directory: Path,
    output: Path,
    motorcycle_directory=None,
    manufacturer_directory=None,
):
    output.mkdir(parents=True, exist_ok=True)
    target = output / "vehicle-catalog.zip"
    temporary = output / "vehicle-catalog.zip.tmp"
    counts = {}
    try:
        import io

        with (
            tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as spool,
            ZipFile(temporary, "w", ZIP_DEFLATED) as archive,
        ):
            with archive.open("vehicles.csv", "w") as raw:
                with io.TextIOWrapper(raw, encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=COLUMNS)
                    writer.writeheader()
                    for row in catalog_rows(
                        store, catalog_directory, motorcycle_directory, manufacturer_directory
                    ):
                        writer.writerow(row)
                        spool.write(json.dumps(row, ensure_ascii=False) + "\n")
                        counts[row["source"]] = counts.get(row["source"], 0) + 1
            spool.seek(0)
            with archive.open("vehicles.json", "w") as raw:
                with io.TextIOWrapper(raw, encoding="utf-8") as stream:
                    stream.write("[")
                    for index, line in enumerate(spool):
                        if index:
                            stream.write(",")
                        stream.write(line.strip())
                    stream.write("]\n")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"archive": str(target), "counts": counts}


def _export_sqlite_tables(path: Path, output: Path, filename: str, tables):
    """Export a consistent read snapshot of scalar source tables."""
    import io

    output.mkdir(parents=True, exist_ok=True)
    target = output / filename
    temporary = output / (filename + ".tmp")
    counts = {}
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
                for table in tables:
                    columns = [
                        row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
                    ]
                    counts[table] = 0
                    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as spool:
                        with archive.open(table + ".csv", "w") as raw:
                            with io.TextIOWrapper(raw, encoding="utf-8", newline="") as stream:
                                writer = csv.DictWriter(stream, fieldnames=columns)
                                writer.writeheader()
                                for row in connection.execute(f'SELECT * FROM "{table}"'):
                                    writer.writerow(dict(row))
                                    spool.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                                    counts[table] += 1
                        spool.seek(0)
                        with archive.open(table + ".json", "w") as raw:
                            with io.TextIOWrapper(raw, encoding="utf-8") as stream:
                                stream.write("[")
                                for index, line in enumerate(spool):
                                    if index:
                                        stream.write(",")
                                    stream.write(line.strip())
                                stream.write("]\n")
            connection.rollback()
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"archive": str(target), "counts": counts}


def export_motorcycle_details(directory: Path, output: Path):
    return _export_sqlite_tables(
        directory / "vehicles.db", output, "motorcycles.zip", ("vehicles", "photos", "spec_columns")
    )


def export_manufacturer_details(directory: Path, output: Path):
    path = directory / "specifications.db"
    tables = ("models", "specification_tables", "specification_cells")
    # Older collected databases remain exportable before their next collector run.
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        for view in (
            "specification_context",
            "powertrain_specs",
            "bus_variant_fields",
            "bus_variant_specs",
        ):
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('view','table') AND name=?", (view,)
            ).fetchone():
                tables += (view,)
    return _export_sqlite_tables(
        path,
        output,
        "manufacturers.zip",
        tables,
    )
