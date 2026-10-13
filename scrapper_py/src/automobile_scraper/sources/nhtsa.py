"""Resumable, type-filtered vPIC catalog. Public model lists do not contain full specs."""

import asyncio
import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

API = "https://vpic.nhtsa.dot.gov/api/vehicles/"
TYPES = {"truck": 3, "bus": 5, "motorcycle": 1}
log = logging.getLogger(__name__)


def timestamp():
    return datetime.now(UTC).isoformat()


class NhtsaCatalog:
    def __init__(self, directory: Path, interval: float = 1.5):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(directory / "vehicles.db")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS vehicles (
                source TEXT NOT NULL,
                make_id INTEGER NOT NULL,
                model_id INTEGER NOT NULL,
                vehicle_type_id INTEGER NOT NULL,
                make_name TEXT NOT NULL,
                model_name TEXT NOT NULL,
                vehicle_category TEXT NOT NULL,
                vehicle_type TEXT NOT NULL,
                source_vehicle_type TEXT NOT NULL,
                source_url TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                detail_level TEXT NOT NULL DEFAULT 'model_listing',
                PRIMARY KEY(source,make_id,model_id,vehicle_type_id)
            );
            CREATE INDEX IF NOT EXISTS ix_catalog_category ON vehicles(vehicle_category);
            CREATE TABLE IF NOT EXISTS work (
                make_id INTEGER NOT NULL,
                vehicle_type_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                make_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                completed_at TEXT,
                PRIMARY KEY(make_id,vehicle_type_id)
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL, attempted INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0
            );
        """)
        self.interval, self.last_request = interval, 0.0
        self.run_id = str(uuid4())

    def close(self):
        self.connection.close()

    async def request(self, client, endpoint):
        url = API + endpoint + "?format=json"
        for attempt in range(3):
            await asyncio.sleep(max(0, self.last_request + self.interval - time.monotonic()))
            self.last_request = time.monotonic()
            try:
                response = await client.get(url)
                capture = self.directory / "raw" / self.run_id
                capture.mkdir(parents=True, exist_ok=True)
                (capture / f"{uuid4()}.json").write_text(
                    json.dumps(
                        {
                            "url": url,
                            "status": response.status_code,
                            "fetched_at": timestamp(),
                            "body": response.text,
                        }
                    ),
                    encoding="utf-8",
                )
                response.raise_for_status()
                data = response.json()
                results = data.get("Results")
                if not isinstance(results, list) or data.get("Count") != len(results):
                    raise ValueError("vPIC response lacks consistent Results/Count")
                if data.get("Message", "").casefold().startswith("error"):
                    raise ValueError(data["Message"])
                return results, url
            except (httpx.HTTPError, ValueError) as exc:
                if isinstance(exc, ValueError):
                    raise
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status < 500 and status != 429:
                        raise
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)
        raise RuntimeError("Unreachable retry state")

    def stage_makes(self, records, category):
        expected = TYPES[category]
        parameters = []
        for row in records:
            if int(row["VehicleTypeId"]) != expected or not row["MakeName"].strip():
                raise ValueError("vPIC make type/name does not match requested category")
            parameters.append((int(row["MakeId"]), expected, category, row["MakeName"]))
        with self.connection:
            self.connection.executemany(
                "INSERT INTO work(make_id,vehicle_type_id,category,make_name) VALUES(?,?,?,?) "
                "ON CONFLICT(make_id,vehicle_type_id) DO UPDATE SET make_name=excluded.make_name",
                parameters,
            )

    def save_models(self, records, make_id, category, url):
        expected = TYPES[category]
        parameters = []
        for row in records:
            if int(row["Make_ID"]) != make_id or int(row["VehicleTypeId"]) != expected:
                raise ValueError("vPIC model identity/type does not match requested make/category")
            if not row["Model_Name"].strip() or not row["Make_Name"].strip():
                raise ValueError("Empty vPIC model/make name")
            parameters.append(
                (
                    "nhtsa_vpic",
                    make_id,
                    int(row["Model_ID"]),
                    expected,
                    row["Make_Name"],
                    row["Model_Name"],
                    category,
                    category,
                    row["VehicleTypeName"],
                    url,
                    timestamp(),
                )
            )
        # Only replace this make/type after the whole response is validated.
        with self.connection:
            self.connection.execute(
                "DELETE FROM vehicles WHERE source='nhtsa_vpic' "
                "AND make_id=? AND vehicle_type_id=?",
                (make_id, expected),
            )
            self.connection.executemany(
                "INSERT INTO vehicles(source,make_id,model_id,vehicle_type_id,make_name,model_name,"
                "vehicle_category,vehicle_type,source_vehicle_type,source_url,fetched_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                parameters,
            )
            self.connection.execute(
                "UPDATE work SET status='completed',last_error=NULL,completed_at=? "
                "WHERE make_id=? AND vehicle_type_id=?",
                (timestamp(), make_id, expected),
            )

    async def run(self, categories, limit=None, refresh=False, client=None):
        if not categories or set(categories) - TYPES.keys():
            raise ValueError("Categories must be truck,bus,motorcycle")
        if client is None:
            async with httpx.AsyncClient(
                timeout=45,
                follow_redirects=True,
                headers={"User-Agent": "AutomobileSpecsScraper/1.0"},
            ) as api:
                return await self.run(categories, limit, refresh, api)
        summary = {"run_id": self.run_id, "attempted": 0, "succeeded": 0, "failed": 0}
        with self.connection:
            self.connection.execute(
                "INSERT INTO runs(id,started_at,status) VALUES(?,?,'running')",
                (self.run_id, timestamp()),
            )
        status = "interrupted"
        try:
            for category in categories:
                records, _ = await self.request(client, f"GetMakesForVehicleType/{category}")
                if not records:
                    raise ValueError(f"No makes returned for {category}")
                self.stage_makes(records, category)
            placeholders = ",".join("?" for _ in categories)
            query = f"SELECT make_id,category FROM work WHERE category IN ({placeholders})"
            if not refresh:
                query += " AND status!='completed'"
            query += " ORDER BY vehicle_type_id,make_id"
            jobs = self.connection.execute(query, categories).fetchall()
            if limit:
                jobs = jobs[:limit]
            for make_id, category in jobs:
                summary["attempted"] += 1
                with self.connection:
                    self.connection.execute(
                        "UPDATE work SET status='started',attempts=attempts+1 "
                        "WHERE make_id=? AND vehicle_type_id=?",
                        (make_id, TYPES[category]),
                    )
                try:
                    rows, url = await self.request(
                        client, f"GetModelsForMakeIdYear/makeId/{make_id}/vehicletype/{category}"
                    )
                    self.save_models(rows, make_id, category, url)
                    summary["succeeded"] += 1
                    log.info(f"catalog_make_completed: {category} {make_id} ({len(rows)} models)")
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    with self.connection:
                        self.connection.execute(
                            "UPDATE work SET status='failed',last_error=? "
                            "WHERE make_id=? AND vehicle_type_id=?",
                            (str(exc), make_id, TYPES[category]),
                        )
                    summary["failed"] += 1
                    log.error(f"catalog_make_failed: {category} {make_id}: {exc}")
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
                        401,
                        403,
                    ):
                        raise
            status = "partial" if summary["failed"] else "completed"
        finally:
            with self.connection:
                self.connection.execute(
                    "UPDATE runs SET finished_at=?,status=?,attempted=?,succeeded=?,failed=? "
                    "WHERE id=?",
                    (
                        timestamp(),
                        status,
                        summary["attempted"],
                        summary["succeeded"],
                        summary["failed"],
                        self.run_id,
                    ),
                )
        return {**summary, **self.coverage()}

    def coverage(self):
        return {
            "database": str(self.directory / "vehicles.db"),
            "model_listings": self.connection.execute("SELECT count(*) FROM vehicles").fetchone()[
                0
            ],
            "categories": dict(
                self.connection.execute(
                    "SELECT vehicle_category,count(*) FROM vehicles GROUP BY vehicle_category"
                )
            ),
            "work_status": dict(
                self.connection.execute("SELECT status,count(*) FROM work GROUP BY status")
            ),
        }
