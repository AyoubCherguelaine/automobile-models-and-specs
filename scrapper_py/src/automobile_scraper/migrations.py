"""Transactional migration/backfill of the original Python SQLite JSON schema."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, select, text
from sqlalchemy.schema import CreateColumn

from .countries import brand_country
from .preprocessing import preprocess_engine, years
from .spec_fields import RAW_COLUMNS
from .storage import (
    RUN_COUNT_COLUMNS,
    Automobile,
    AutomobilePhoto,
    Base,
    Brand,
    Engine,
    Run,
    create_vehicle_view,
    unique_columns,
)
from .vehicle_information import classify_vehicle, title_information


def preprocess_database(store) -> dict:
    if store.engine.dialect.name != "sqlite":
        raise ValueError("The in-place preprocessing migration supports SQLite only")
    inspector = inspect(store.engine)
    tables = set(inspector.get_table_names())
    if not tables:
        store.initialize()
        return {"migrated": False, "backup": None, **store.counts()}
    required = {"brands", "automobiles", "engines"}
    if not required.issubset(tables):
        raise ValueError("Incomplete database: expected Python brands, automobiles, engines")
    engine_columns = {column["name"] for column in inspector.get_columns("engines")}
    model_columns = {column["name"] for column in inspector.get_columns("automobiles")}
    run_columns = (
        {item["name"] for item in inspector.get_columns("scrape_runs")}
        if "scrape_runs" in tables
        else set()
    )
    legacy = "specs" in engine_columns
    missing_columns = any(
        not {column.name for column in model.__table__.columns}.issubset(
            {item["name"] for item in inspector.get_columns(model.__tablename__)}
        )
        for model in (Brand, Automobile, Engine, Run)
        if model.__tablename__ in tables
    )
    migrating = legacy or "photos" in model_columns or "summary" in run_columns or missing_columns
    for model in (Brand, Automobile, Engine):
        name = model.__tablename__
        constraints = unique_columns(store.engine, inspector, name)
        expected = ("automobile_id", "other_id") if model is Engine else ("url_hash",)
        if expected not in constraints:
            raise ValueError("Expected the Python schema's uniqueness constraints")
    backup = None
    database = store.engine.url.database
    if database and database != ":memory:" and migrating:
        source = Path(database)
        target = source.with_name(f"{source.stem}.before-columns-{uuid4().hex[:12]}.db")
        # sqlite3 backup includes committed WAL pages and never edits the source.
        with sqlite3.connect(source) as original, sqlite3.connect(target) as destination:
            original.backup(destination)
        backup = str(target)
    with store.engine.connect() as connection:
        # Explicit BEGIN makes SQLite DDL and all backfills one atomic migration.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if migrating and sqlite3.sqlite_version_info < (3, 35, 0):
                raise ValueError("SQLite 3.35+ is required to remove the JSON columns")
            connection.execute(text("DROP VIEW IF EXISTS vehicle_specs"))
            for model in (Brand, Automobile, Engine, Run):
                if model.__tablename__ not in tables:
                    continue
                present = {item["name"] for item in inspector.get_columns(model.__tablename__)}
                for column in model.__table__.columns:
                    if column.name not in present:
                        definition = str(CreateColumn(column).compile(dialect=connection.dialect))
                        connection.exec_driver_sql(
                            f'ALTER TABLE "{model.__tablename__}" ADD COLUMN {definition}'
                        )
            Base.metadata.create_all(connection)
            for row in connection.execute(select(Brand.id, Brand.name, Brand.country)).mappings():
                if row["country"] is None:
                    connection.execute(
                        Brand.__table__.update()
                        .where(Brand.id == row["id"])
                        .values(country=brand_country(row["name"]), updated_at=Brand.updated_at)
                    )
            model_rows = connection.exec_driver_sql(
                "SELECT a.*, b.name AS brand_name FROM automobiles a "
                "JOIN brands b ON a.brand_id=b.id"
            )
            for row in model_rows.mappings():
                information = {
                    key: value
                    for key, value in title_information(row["name"], row["brand_name"]).items()
                    if row[key] is None
                }
                information.update(classify_vehicle({**dict(row), **information}))
                connection.execute(
                    Automobile.__table__.update()
                    .where(Automobile.id == row["id"])
                    .values(
                        **years(row["name"], row["url"]),
                        **information,
                        updated_at=Automobile.updated_at,
                    )
                )
                if "photos" in model_columns:
                    photos = json.loads(row["photos"]) if row["photos"] else []
                    if photos is not None:
                        if not isinstance(photos, list) or any(
                            not isinstance(url, str) for url in photos
                        ):
                            raise ValueError(f"Invalid photos for model {row['id']}")
                        for position, url in enumerate(photos):
                            connection.execute(
                                AutomobilePhoto.__table__.insert().values(
                                    automobile_id=row["id"], position=position, url=url
                                )
                            )
            rows = connection.exec_driver_sql("SELECT * FROM engines")
            for batch in rows.mappings().partitions(500):
                parameters = []
                for row in batch:
                    specs = json.loads(row["specs"]) if legacy and row["specs"] else {}
                    flat = None if legacy else {key: row[key] for key in RAW_COLUMNS}
                    parameters.append(
                        {"row_id": row["id"], **preprocess_engine(specs, row["name"], flat)}
                    )
                if parameters:
                    keys = [key for key in parameters[0] if key != "row_id"]
                    statement = "UPDATE engines SET " + ", ".join(f'"{key}"=:{key}' for key in keys)
                    connection.execute(text(statement + " WHERE id=:row_id"), parameters)
            if legacy:
                connection.exec_driver_sql("ALTER TABLE engines DROP COLUMN specs")
            if "photos" in model_columns:
                connection.exec_driver_sql("ALTER TABLE automobiles DROP COLUMN photos")
            if "summary" in run_columns:
                for row in connection.exec_driver_sql(
                    "SELECT id, summary FROM scrape_runs"
                ).mappings():
                    summary = json.loads(row["summary"]) if row["summary"] else {}
                    counts = {column: summary.get(column, 0) for column in RUN_COUNT_COLUMNS}
                    connection.execute(
                        Run.__table__.update().where(Run.id == row["id"]).values(**counts)
                    )
                connection.exec_driver_sql("ALTER TABLE scrape_runs DROP COLUMN summary")
            for table in Base.metadata.sorted_tables:
                for index in table.indexes:
                    index.create(connection, checkfirst=True)
            create_vehicle_view(connection)
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise ValueError("Foreign key validation failed; migration rolled back")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    store.initialize()
    return {"migrated": migrating, "backup": backup, **store.counts()}
