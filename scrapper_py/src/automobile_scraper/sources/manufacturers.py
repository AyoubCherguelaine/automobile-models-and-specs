"""Capture complete official specification matrices without collapsing variant options."""

import hashlib
import json
import re
import sqlite3
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..manufacturer_views import create_bus_views, create_powertrain_view
from ..parsers import ParseError
from .nhtsa import timestamp

SOURCES = (
    (
        "mercedes_benz",
        "Mercedes-Benz",
        "Citaro",
        "bus",
        "city_bus",
        "https://www.mercedes-benz-bus.com/int/en/models/citaro/facts-citaro.html",
    ),
    (
        "scania",
        "Scania",
        "R-series",
        "truck",
        "heavy_truck",
        "https://www.scania.com/uk/en/home/products/trucks/r-series/r-series-specifications.html",
    ),
    *(
        (
            "scania",
            "Scania",
            f"{series.upper()}-series",
            "truck",
            "heavy_truck" if series in {"s", "g"} else "truck",
            f"https://www.scania.com/uk/en/home/products/trucks/{series}-series/"
            f"{series}-series-specifications.html",
        )
        for series in ("s", "g", "p", "l")
    ),
    *(
        (
            "mercedes_benz",
            "Mercedes-Benz",
            model,
            "bus",
            subtype,
            f"https://www.mercedes-benz-bus.com/int/en/models/{slug}/facts-{slug}.html",
        )
        for model, slug, subtype in (
            ("eCitaro", "ecitaro", "city_bus"),
            ("Tourismo", "tourismo-rhd", "coach"),
            ("Intouro", "intouro", "intercity_bus"),
            ("Conecto", "conecto", "city_bus"),
            ("CapaCity", "capacity", "articulated_bus"),
        )
    ),
    *(
        (
            "setra",
            "Setra",
            model,
            "bus",
            subtype,
            f"https://www.setra-bus.com/int/en/models/{slug}/facts-{slug}.html",
        )
        for model, slug, subtype in (
            ("ComfortClass 500 HD", "cc-hd-models", "coach"),
            ("TopClass 500", "tc-hdh-models", "coach"),
            ("TopClass S 531 DT", "tc-dt-models", "double_decker_coach"),
            ("MultiClass 500", "mc-le-models", "low_entry_bus"),
        )
    ),
)


def specification_tables(html, model):
    page = BeautifulSoup(html, "lxml")
    title = page.title.get_text(" ", strip=True) if page.title else ""
    if model.casefold() not in title.casefold():
        raise ParseError("Manufacturer response is not the requested model page")
    result = []
    for position, table in enumerate(page.select("table")):
        heading = table.find_previous(["h2", "h3", "h4"])
        heading = heading.get_text(" ", strip=True) if heading else title
        cells = []
        occupied = set()
        for row_number, row in enumerate(table.select("tr")):
            column = 0
            for cell in row.find_all(["td", "th"], recursive=False):
                while (row_number, column) in occupied:
                    column += 1
                rowspan = int(cell.get("rowspan", 1))
                colspan = int(cell.get("colspan", 1))
                if not 1 <= rowspan <= 1000 or not 1 <= colspan <= 1000:
                    raise ParseError("Unsupported manufacturer table span")
                cells.append(
                    (
                        row_number,
                        column,
                        rowspan,
                        colspan,
                        cell.name == "th",
                        cell.get_text(" ", strip=True),
                    )
                )
                for r in range(row_number, row_number + rowspan):
                    for c in range(column, column + colspan):
                        if (r, c) in occupied:
                            raise ParseError("Overlapping manufacturer table spans")
                        occupied.add((r, c))
                column += colspan
        if cells:
            result.append((position, heading, cells))
    if not result:
        raise ParseError("No manufacturer specification tables")
    return title, result


def specification_contexts(html):
    """Read explicit tab labels through accessible panel references."""
    page = BeautifulSoup(html, "lxml")
    result = {}
    for position, table in enumerate(page.select("table")):
        panel = table.find_parent(attrs={"role": "tabpanel"})
        labels = []
        if panel:
            for reference in panel.get("aria-labelledby", "").split():
                label = page.find(id=reference)
                if label:
                    text = label.get_text(" ", strip=True)
                    if text:
                        labels.append(text)
        result[position] = " / ".join(labels) or None
    return result


class ManufacturerStore:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(directory / "specifications.db")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS models(
                source_url TEXT PRIMARY KEY, source TEXT NOT NULL, make_name TEXT NOT NULL,
                model_name TEXT NOT NULL, vehicle_category TEXT NOT NULL,
                vehicle_type TEXT NOT NULL,
                page_title TEXT NOT NULL, html_sha256 TEXT NOT NULL, fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS specification_tables(
                source_url TEXT REFERENCES models(source_url), table_position INTEGER,
                heading TEXT NOT NULL, PRIMARY KEY(source_url,table_position)
            );
            CREATE TABLE IF NOT EXISTS specification_cells(
                source_url TEXT, table_position INTEGER, row_position INTEGER,
                column_position INTEGER,
                row_span INTEGER NOT NULL, column_span INTEGER NOT NULL, is_header INTEGER NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(source_url,table_position,row_position,column_position),
                FOREIGN KEY(source_url,table_position)
                    REFERENCES specification_tables(source_url,table_position)
            );
            CREATE TABLE IF NOT EXISTS work(
                source_url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS specification_context(
                source_url TEXT, table_position INTEGER, tab_label TEXT,
                PRIMARY KEY(source_url,table_position),
                FOREIGN KEY(source_url,table_position)
                    REFERENCES specification_tables(source_url,table_position) ON DELETE CASCADE
            );
        """)
        with self.connection:
            self.connection.execute("BEGIN")
            create_powertrain_view(self.connection)
            create_bus_views(self.connection)

    def save(self, source, html):
        identifier, make, model, category, subtype, url = source
        title, tables = specification_tables(html, model)
        contexts = specification_contexts(html)
        with self.connection:
            self.connection.execute(
                "INSERT INTO models VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source_url) DO UPDATE SET "
                "page_title=excluded.page_title,html_sha256=excluded.html_sha256,"
                "fetched_at=excluded.fetched_at",
                (
                    url,
                    identifier,
                    make,
                    model,
                    category,
                    subtype,
                    title,
                    hashlib.sha256(html.encode()).hexdigest(),
                    timestamp(),
                ),
            )
            self.connection.execute("DELETE FROM specification_cells WHERE source_url=?", (url,))
            self.connection.execute("DELETE FROM specification_tables WHERE source_url=?", (url,))
            for position, heading, cells in tables:
                self.connection.execute(
                    "INSERT INTO specification_tables VALUES(?,?,?)", (url, position, heading)
                )
                self.connection.execute(
                    "INSERT INTO specification_context VALUES(?,?,?)",
                    (url, position, contexts.get(position)),
                )
                self.connection.executemany(
                    "INSERT INTO specification_cells VALUES(?,?,?,?,?,?,?,?)",
                    [(url, position, *cell) for cell in cells],
                )
            self.connection.execute(
                "UPDATE work SET status='completed',last_error=NULL WHERE source_url=?", (url,)
            )
        return {
            "source_url": url,
            "tables": len(tables),
            "cells": sum(len(cells) for _, _, cells in tables),
        }

    def enrich_cached_context(self, raw_dir):
        """Backfill context only from the exact capture that produced the saved matrix."""
        missing = dict(
            self.connection.execute("""
            SELECT m.source_url,m.html_sha256 FROM models m
            WHERE EXISTS(
                SELECT 1 FROM specification_tables t
                LEFT JOIN specification_context c USING(source_url,table_position)
                WHERE t.source_url=m.source_url AND c.source_url IS NULL
            )
        """)
        )
        updated = 0
        invalid = 0
        for metadata_path in sorted(raw_dir.glob("*/*.json")) if missing else ():
            if not missing:
                break
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    raise ValueError("Capture metadata must be an object")
            except (OSError, ValueError):
                invalid += 1
                continue
            url = metadata.get("url")
            if (
                not isinstance(url, str)
                or url not in missing
                or metadata.get("method") != "GET"
                or metadata.get("status") != 200
            ):
                continue
            capture = metadata_path.with_suffix(".html")
            if not capture.is_file():
                continue
            try:
                html = capture.read_bytes().decode("utf-8")
            except (OSError, ValueError):
                invalid += 1
                continue
            if hashlib.sha256(html.encode()).hexdigest() != missing[url]:
                continue
            contexts = specification_contexts(html)
            with self.connection:
                positions = self.connection.execute(
                    "SELECT table_position FROM specification_tables WHERE source_url=?", (url,)
                ).fetchall()
                self.connection.executemany(
                    "INSERT OR IGNORE INTO specification_context VALUES(?,?,?)",
                    [(url, position, contexts.get(position)) for (position,) in positions],
                )
            missing.pop(url)
            updated += 1
        return {
            "models_updated": updated,
            "models_without_matching_capture": len(missing),
            "invalid_captures": invalid,
        }

    def close(self):
        self.connection.close()


async def collect_manufacturers(settings, directory: Path, refresh=False):
    from uuid import uuid4

    from ..fetching import Fetcher

    store = ManufacturerStore(directory)
    result = {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "sources": [],
        "failures": [],
    }
    fetchers = {}
    run_id = str(uuid4())
    try:
        result["cached_context"] = store.enrich_cached_context(directory / "raw")
        # Seed all jobs before requests so a stopped run still exposes remaining work.
        with store.connection:
            store.connection.executemany(
                "INSERT OR IGNORE INTO work(source_url) VALUES(?)",
                [(source[-1],) for source in SOURCES],
            )
        async with AsyncExitStack() as stack:
            for source in SOURCES:
                url = source[-1]
                with store.connection:
                    status = store.connection.execute(
                        "SELECT status FROM work WHERE source_url=?", (url,)
                    ).fetchone()[0]
                    if status == "completed" and not refresh:
                        result["skipped"] += 1
                        continue
                    store.connection.execute(
                        "UPDATE work SET status='started',attempts=attempts+1 WHERE source_url=?",
                        (url,),
                    )
                result["attempted"] += 1
                try:
                    host = urlsplit(url)
                    origin = f"{host.scheme}://{host.netloc}"
                    if origin not in fetchers:
                        config = settings.model_copy(
                            update={
                                "data_dir": directory,
                                "transport": "http",
                                "base_url": origin,
                            }
                        )
                        fetchers[origin] = await stack.enter_async_context(Fetcher(config, run_id))
                    html = await fetchers[origin].request(url)
                    result["sources"].append(store.save(source, html))
                    result["succeeded"] += 1
                except (ValueError, RuntimeError) as exc:
                    result["failed"] += 1
                    result["failures"].append({"source_url": url, "error": str(exc)})
                    with store.connection:
                        store.connection.execute(
                            "UPDATE work SET status='failed',last_error=? WHERE source_url=?",
                            (str(exc), url),
                        )
                    if re.search(r"HTTP (?:401|403)\b", str(exc)):
                        result["stopped_reason"] = "authorization_failure"
                        break
        result["requests"] = sum(fetcher.request_count for fetcher in fetchers.values())
        result["retried"] = sum(fetcher.retry_count for fetcher in fetchers.values())
        result["work_status"] = dict(
            store.connection.execute("SELECT status,count(*) FROM work GROUP BY status")
        )
        return result
    finally:
        store.close()
