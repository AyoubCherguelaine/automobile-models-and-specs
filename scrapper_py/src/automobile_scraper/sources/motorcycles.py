"""Motorcycle/powersports detail parser and scalar SQLite storage, separate from car variants."""

import re
import sqlite3
from pathlib import Path

from ..domain import url_key
from ..parsers import ParseError, parse_automobile, soup, text
from ..preprocessing import preprocess_engine, years
from ..spec_fields import ENGINE_COLUMNS, FLOAT_COLUMNS, INTEGER_COLUMNS
from ..vehicle_information import VEHICLE_COLUMNS

# Source segment evidence matters: the moto database includes ATVs and snowmobiles too.
SEGMENTS = {
    "classic",
    "sport",
    "sports",
    "supersport",
    "super sport",
    "sport touring",
    "touring",
    "naked",
    "naked bike",
    "cruiser",
    "custom / chopper",
    "custom",
    "chopper",
    "enduro",
    "cross / motocross",
    "motocross",
    "trial",
    "supermoto",
    "super moto",
    "adventure",
    "dual sport",
    "dual-sport",
    "scooter",
    "moped",
    "cafe racer",
    "street",
    "offroad",
}
ALIASES = {
    ("engine specs", "type"): "cylinders_raw",
    ("engine specs", "engine type"): "cylinders_raw",
    ("engine specs", "cylinders"): "cylinders_raw",
    ("engine specs", "displacement"): "size",
    ("engine specs", "horsepower"): "power",
    ("engine specs", "power"): "power",
    ("engine specs", "torque"): "torque",
    ("engine specs", "fuel system"): "fuel_system",
    ("engine specs", "fuel"): "fuel",
    ("transmission specs", "gearbox"): "T_gearbox",
    ("chassis specs", "front brake"): "brakes_front",
    ("chassis specs", "rear brake"): "brakes_rear",
    ("dimensions", "overall length"): "length",
    ("dimensions", "overall width"): "width",
    ("dimensions", "overall height"): "height",
    ("dimensions", "wheelbase"): "wheelbase",
    ("dimensions", "ground clearance"): "ground_clearance",
    ("dimensions", "fuel capacity"): "fuel_capacity",
    ("dimensions", "weight"): "unladen_weight",
    ("performance specs", "top speed"): "top_speed",
}


def key(value):
    return re.sub(r"\s+", " ", value).strip(": ").casefold()


def field_column(section, label):
    value = "spec_" + re.sub(r"[^a-z0-9]+", "_", f"{section}_{label}".casefold()).strip("_")
    if not value.removeprefix("spec_"):
        raise ParseError("Empty specification label")
    return value


def parse_motorcycle(html, url):
    record = parse_automobile(html, url)
    page = soup(html)
    # Motorcycle header metadata uses inline paragraphs rather than car summary tables.
    for label_node in page.select("p.nomgtop b"):
        label = key(label_node.get_text(" ", strip=True))
        column = {"segment": "segment", "production years": "production_years_text"}.get(label)
        if column:
            parts = []
            for node in label_node.next_siblings:
                if getattr(node, "name", None) in ("br", "b"):
                    break
                parts.append(
                    node.get_text(" ", strip=True) if hasattr(node, "get_text") else str(node)
                )
            value = " ".join(parts).strip()
            if value:
                record.information[column] = value
                record.information["metadata_source"] = "page_metadata"
    if record.photos:
        record.information["main_image_url"] = record.photos[0]
    tables = page.select(".enginedata.engine-inline table.techdata")
    if not tables:
        raise ParseError("No motorcycle technical tables; source markup may have changed")
    fields, registry, normalized = {}, {}, {}
    for table in tables:
        section = key(text(table.select_one(".title")))
        if not section:
            raise ParseError("Motorcycle specification section has no title")
        for row in table.select("tr"):
            cells = row.select("td")
            if len(cells) != 2:
                continue
            label = key(text(cells[0]))
            column = field_column(section, label)
            identity = (section, label)
            if column in registry and registry[column] != identity:
                raise ParseError(f"Conflicting specification labels: {column}")
            value = cells[1].get_text("\n", strip=True)
            if column in fields and fields[column] != value:
                raise ParseError(
                    f"Multiple technical values for {identity}; variants need separation"
                )
            registry[column], fields[column] = identity, value
            if identity in ALIASES:
                normalized[ALIASES[identity]] = value
    if not fields:
        raise ParseError("Motorcycle page has no specification values")
    segment = (record.information.get("segment") or "").casefold()
    category = "unknown"
    if segment in SEGMENTS:
        category = "motorcycle"
    elif "atv" in segment or "all-terrain" in segment:
        category = "atv"
    elif "utv" in segment or "side-by-side" in segment:
        category = "utv"
    elif "snowmobile" in segment:
        category = "snowmobile"
    record.information.update(
        vehicle_category=category,
        vehicle_type=category,
        classification_source="segment" if category != "unknown" else "insufficient_evidence",
    )
    return record, {**preprocess_engine({}, record.name, normalized), **fields}, registry


class MotorcycleStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(directory / "vehicles.db")
        self.connection.execute("PRAGMA foreign_keys=ON")
        columns = {}
        for column in (*VEHICLE_COLUMNS, *ENGINE_COLUMNS):
            columns[column] = (
                "REAL"
                if column in FLOAT_COLUMNS
                else "INTEGER"
                if column in INTEGER_COLUMNS
                or column
                in (
                    "doors",
                    "seats",
                    "autonomy_level",
                    "supports_apple_carplay",
                    "supports_android_auto",
                )
                else "TEXT"
            )
        definitions = ",".join(f'"{column}" {kind}' for column, kind in columns.items())
        self.connection.executescript(f"""
            CREATE TABLE IF NOT EXISTS vehicles(
                url_hash TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, make_name TEXT NOT NULL, description TEXT,
                year INTEGER, year_end INTEGER, year_source TEXT,
                {definitions}
            );
            CREATE TABLE IF NOT EXISTS photos(
                url_hash TEXT REFERENCES vehicles(url_hash), position INTEGER, url TEXT NOT NULL,
                PRIMARY KEY(url_hash,position)
            );
            CREATE TABLE IF NOT EXISTS spec_columns(
                column_name TEXT PRIMARY KEY, section TEXT NOT NULL, label TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work(
                url TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_moto_category ON vehicles(vehicle_category);
        """)

    def close(self):
        self.connection.close()

    def save(self, record, fields, registry):
        connection = self.connection
        with connection:
            connection.execute("BEGIN")
            existing = {row[1] for row in connection.execute("PRAGMA table_info(vehicles)")}
            for column, (section, label) in registry.items():
                previous = connection.execute(
                    "SELECT section,label FROM spec_columns WHERE column_name=?", (column,)
                ).fetchone()
                if previous and previous != (section, label):
                    raise ParseError(f"Specification column collision: {column}")
                connection.execute(
                    "INSERT OR IGNORE INTO spec_columns VALUES(?,?,?)", (column, section, label)
                )
                if column not in existing:
                    if not re.fullmatch(r"[a-z0-9_]+", column):
                        raise ParseError("Invalid specification column name")
                    connection.execute(f'ALTER TABLE vehicles ADD COLUMN "{column}" TEXT')
            hash_key = url_key(str(record.url))
            empty_specs = {column: None for column in existing if column.startswith("spec_")}
            values = dict(
                url_hash=hash_key,
                url=str(record.url),
                name=record.name,
                make_name=record.brand_name,
                description=record.description,
                **years(record.name, str(record.url)),
                **record.information,
                **(empty_specs | fields),
            )
            keys = list(values)
            quoted = ",".join(f'"{column}"' for column in keys)
            placeholders = ",".join("?" for _ in keys)
            updates = ",".join(
                f'"{column}"=excluded."{column}"' for column in keys if column != "url_hash"
            )
            connection.execute(
                f"INSERT INTO vehicles({quoted}) VALUES({placeholders}) "
                f"ON CONFLICT(url_hash) DO UPDATE SET {updates}",
                list(values.values()),
            )
            connection.execute("DELETE FROM photos WHERE url_hash=?", (hash_key,))
            connection.executemany(
                "INSERT INTO photos VALUES(?,?,?)",
                [(hash_key, i, photo) for i, photo in enumerate(record.photos or [])],
            )
            connection.execute(
                "UPDATE work SET status='completed',last_error=NULL WHERE url=?", (str(record.url),)
            )


def discover_motorcycle_urls(html, url, kind):
    """Walk directory -> brand -> family -> year-specific detail without unrelated links."""
    from urllib.parse import urldefrag, urljoin, urlsplit

    from ..parsers import parse_brands

    if kind == "directory":
        return [(str(brand.url), "brand") for brand in parse_brands(html, url)]
    page = soup(html)
    selector = ".carmod .padcol2 > a[href]" if kind == "brand" else ".carmodel .years h2 a[href]"
    found = []
    for node in page.select(selector):
        target = urldefrag(urljoin(url, node["href"]))[0]
        parsed = urlsplit(target)
        if parsed.netloc != urlsplit(url).netloc or not parsed.path.startswith("/moto/"):
            raise ParseError("Unexpected motorcycle discovery URL")
        if kind == "family" and not parsed.path.endswith(".html"):
            raise ParseError("Motorcycle generation URL is not a detail page")
        if kind == "brand" and not parsed.path.startswith(urlsplit(url).path.rstrip("/") + "/"):
            raise ParseError("Motorcycle family is outside requested brand")
        found.append((target, "family" if kind == "brand" else "detail"))
    if not found:
        raise ParseError(f"No motorcycle {kind} results; source markup may have changed")
    return list(dict.fromkeys(found))


async def collect_motorcycles(settings, directory, limit=None, refresh=False, fetcher=None):
    import logging
    from uuid import uuid4

    from ..fetching import Fetcher

    if fetcher is None:
        async with Fetcher(
            settings.model_copy(update={"data_dir": directory}), str(uuid4())
        ) as client:
            return await collect_motorcycles(settings, directory, limit, refresh, client)
    store = MotorcycleStore(directory)
    connection = store.connection
    summary = {"attempted": 0, "succeeded": 0, "failed": 0}
    consecutive_failures = 0
    log = logging.getLogger(__name__)
    try:
        with connection:
            connection.execute(
                "UPDATE work SET status='queued' WHERE status IN ('started','failed')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO work(url,kind) VALUES(?,'directory')",
                (settings.base_url + "/moto/",),
            )
            # Always rediscover the directory, keeping completed lower-level work resumable.
            if refresh:
                connection.execute("UPDATE work SET status='queued'")
            else:
                connection.execute("UPDATE work SET status='queued' WHERE kind='directory'")
        while not limit or summary["attempted"] < limit:
            job = connection.execute(
                "SELECT url,kind FROM work WHERE status='queued' "
                "ORDER BY CASE kind WHEN 'directory' THEN 0 WHEN 'detail' THEN 1 "
                "WHEN 'family' THEN 2 ELSE 3 END, rowid LIMIT 1"
            ).fetchone()
            if not job:
                break
            url, kind = job
            # Select each failed unit once per run; subsequent runs retry it.
            with connection:
                connection.execute(
                    "UPDATE work SET status='started',attempts=attempts+1 WHERE url=?", (url,)
                )
            summary["attempted"] += 1
            try:
                html = await fetcher.request(url)
                if kind == "detail":
                    record, fields, registry = parse_motorcycle(html, url)
                    store.save(record, fields, registry)
                else:
                    urls = discover_motorcycle_urls(html, url, kind)
                    with connection:
                        connection.executemany(
                            "INSERT OR IGNORE INTO work(url,kind) VALUES(?,?)", urls
                        )
                        connection.execute(
                            "UPDATE work SET status='completed',last_error=NULL WHERE url=?", (url,)
                        )
                consecutive_failures = 0
                summary["succeeded"] += 1
                log.info("motorcycle_completed", extra={"url": url})
            except (ValueError, RuntimeError) as exc:
                with connection:
                    connection.execute(
                        "UPDATE work SET status='failed',last_error=? WHERE url=?", (str(exc), url)
                    )
                summary["failed"] += 1
                log.error("motorcycle_failed", extra={"url": url, "error": str(exc)})
                consecutive_failures += 1
                if consecutive_failures >= 3 or re.search(r"HTTP (?:401|403)\b", str(exc)):
                    break
        return {
            **summary,
            "database": str(directory / "vehicles.db"),
            "models": connection.execute("SELECT count(*) FROM vehicles").fetchone()[0],
            "work_status": dict(
                connection.execute("SELECT status,count(*) FROM work GROUP BY status")
            ),
        }
    finally:
        store.close()
