"""Coverage and classification reports distinguish stored rows from complete metadata."""

from sqlalchemy import func, select

from .storage import Automobile, WorkItem
from .vehicle_information import VEHICLE_COLUMNS


def coverage_report(store) -> dict:
    with store.session() as session:
        categories = dict(
            session.execute(
                select(Automobile.vehicle_category, func.count()).group_by(
                    Automobile.vehicle_category
                )
            ).all()
        )
        types = dict(
            session.execute(
                select(Automobile.vehicle_type, func.count()).group_by(Automobile.vehicle_type)
            ).all()
        )
        work = dict(
            session.execute(select(WorkItem.status, func.count()).group_by(WorkItem.status)).all()
        )
        populated = {
            column: session.scalar(
                select(func.count())
                .select_from(Automobile)
                .where(getattr(Automobile, column).is_not(None))
            )
            for column in VEHICLE_COLUMNS
        }
    counts = store.counts()
    return {
        **counts,
        "vehicle_categories": categories,
        "vehicle_types": types,
        "unclassified_models": categories.get("unknown", 0) + categories.get(None, 0),
        "populated_vehicle_fields": populated,
        "work_status": work,
    }


def catalog_coverage(directory):
    """Read catalog state without acquiring its writer lock or changing its schema."""
    import sqlite3

    path = directory / "vehicles.db"
    if not path.is_file():
        return {"database": str(path), "status": "not_collected"}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return {
            "database": str(path),
            "categories": dict(
                connection.execute(
                    "SELECT vehicle_category,count(*) FROM vehicles GROUP BY vehicle_category"
                )
            ),
            "work_status": dict(
                connection.execute("SELECT status,count(*) FROM work GROUP BY status")
            ),
            "latest_run": connection.execute(
                "SELECT id,status,attempted,succeeded,failed FROM runs "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone(),
            "detail_level": "model_listing",
        }


def motorcycle_coverage(directory):
    import sqlite3

    path = directory / "vehicles.db"
    if not path.is_file():
        return {"database": str(path), "status": "not_collected"}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return {
            "database": str(path),
            "models": connection.execute("SELECT count(*) FROM vehicles").fetchone()[0],
            "source_fields": connection.execute("SELECT count(*) FROM spec_columns").fetchone()[0],
            "categories": dict(
                connection.execute(
                    "SELECT vehicle_category,count(*) FROM vehicles GROUP BY vehicle_category"
                )
            ),
            "work_status": dict(
                connection.execute("SELECT status,count(*) FROM work GROUP BY status")
            ),
        }


def manufacturer_coverage(directory):
    """Report captured specification matrices without acquiring the collection lock."""
    import sqlite3

    path = directory / "specifications.db"
    if not path.is_file():
        return {"database": str(path), "status": "not_collected"}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("BEGIN")
        views = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='view'")
        }
        return {
            "database": str(path),
            "models": connection.execute("SELECT count(*) FROM models").fetchone()[0],
            "specification_tables": connection.execute(
                "SELECT count(*) FROM specification_tables"
            ).fetchone()[0],
            "specification_cells": connection.execute(
                "SELECT count(*) FROM specification_cells"
            ).fetchone()[0],
            "powertrain_options": (
                connection.execute("SELECT count(*) FROM powertrain_specs").fetchone()[0]
                if "powertrain_specs" in views
                else None
            ),
            "bus_variants": (
                connection.execute("SELECT count(*) FROM bus_variant_specs").fetchone()[0]
                if "bus_variant_specs" in views
                else None
            ),
            "bus_variant_fields": (
                connection.execute("SELECT count(*) FROM bus_variant_fields").fetchone()[0]
                if "bus_variant_fields" in views
                else None
            ),
            "categories": dict(
                connection.execute(
                    "SELECT vehicle_category,count(*) FROM models GROUP BY vehicle_category"
                )
            ),
            "work_status": dict(
                connection.execute("SELECT status,count(*) FROM work GROUP BY status")
            ),
            "detail_level": "specification_tables",
        }
