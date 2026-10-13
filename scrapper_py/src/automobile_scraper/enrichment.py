"""Replay cached page metadata into vehicle columns without rewriting specs or checkpoints."""

import json
import logging
from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import func, select

from .domain import url_key
from .storage import Automobile, Brand
from .vehicle_information import VEHICLE_COLUMNS, classify_vehicle, extract_vehicle_information

log = logging.getLogger(__name__)


def enrich_cached_pages(store, raw_dir: Path) -> dict:
    if not raw_dir.is_dir():
        raise ValueError(f"Raw HTML directory does not exist: {raw_dir}")
    with store.session() as session:
        models = {
            row.url_hash: row.id
            for row in session.execute(select(Automobile.id, Automobile.url_hash))
        }
    latest = {}
    summary = {"cached_pages": 0, "matched_models": 0, "updated_models": 0, "failed": 0}
    for metadata_path in sorted(raw_dir.glob("*/*.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("method") != "GET" or metadata.get("status") != 200:
                continue
            key = url_key(metadata.get("url", ""))
            if key not in models:
                continue
            path = metadata_path.with_suffix(".html")
            if not path.is_file():
                continue
            timestamp = float(metadata.get("captured_at") or path.stat().st_mtime)
            summary["cached_pages"] += 1
            if key not in latest or timestamp > latest[key][0]:
                latest[key] = (timestamp, path)
        except (OSError, ValueError, TypeError) as exc:
            summary["failed"] += 1
            log.error("cached_metadata_failed", extra={"error": str(exc)})
    summary["matched_models"] = len(latest)
    for key, (_, path) in latest.items():
        try:
            page = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
            title = page.select_one(".newstitle")
            if title is None:
                raise ValueError("Cached page has no vehicle title")
            with store.session() as session, session.begin():
                row = session.get(Automobile, models[key])
                brand = session.get(Brand, row.brand_id)
                fields = extract_vehicle_information(
                    page, title.get_text(" ", strip=True), brand.name, row.url
                )
                for column, value in fields.items():
                    if value is not None:
                        setattr(row, column, value)
                # Missing values preserve stored evidence, so classification must use
                # that same merged metadata rather than the incomplete cached page.
                for column, value in classify_vehicle(
                    {column: getattr(row, column) for column in VEHICLE_COLUMNS}
                ).items():
                    setattr(row, column, value)
            summary["updated_models"] += 1
            if summary["updated_models"] % 50 == 0:
                log.info(f"cached_enrichment_progress: {summary['updated_models']} models")
        except (OSError, ValueError, TypeError) as exc:
            summary["failed"] += 1
            log.error("cached_vehicle_failed", extra={"url": str(path), "error": str(exc)})
    with store.session() as session:
        summary["coverage"] = {
            column: session.scalar(
                select(func.count())
                .select_from(Automobile)
                .where(getattr(Automobile, column).is_not(None))
            )
            for column in VEHICLE_COLUMNS
        }
    return summary
