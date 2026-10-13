import asyncio
import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from filelock import FileLock

from .config import Settings
from .exporting import export_data
from .importing import import_archive
from .pipeline import Pipeline
from .storage import Store

app = typer.Typer(no_args_is_help=True, help="Scrape and export AutoEvolution automobile specs.")


class Mode(StrEnum):
    new = "new"
    all = "all"
    update = "update"
    resume = "resume"
    retry_failed = "retry-failed"
    brands = "brands"


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {"level": record.levelname, "event": record.getMessage()}
        for key in ("run_id", "url", "attempt", "error"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, ensure_ascii=False)


def execute(operation, initialize=True, wait=False):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    store = None
    try:
        settings = Settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        # One process per data directory; workers within it are bounded by concurrency.
        with FileLock(str(settings.data_dir / "pipeline.lock"), timeout=-1 if wait else 0):
            store = Store(settings.database_url)
            if initialize:
                store.initialize()
            result = operation(settings, store)
            typer.echo(json.dumps(result, ensure_ascii=False))
            if isinstance(result, dict) and result.get("failed", 0):
                raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if store:
            store.close()


@app.command("init-db")
def init_db():
    """Create the Python schema in a new, dedicated database."""
    execute(lambda settings, store: store.counts())


@app.command()
def scrape(mode: Mode = Mode.new, limit: int | None = typer.Option(None, min=1)):
    """Scrape new/all models, update stored models, or resume queued/failed work."""
    execute(lambda settings, store: asyncio.run(Pipeline(settings, store).run(mode.value, limit)))


@app.command("import-json")
def import_json(archive: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]):
    """Import a Laravel or Python JSON ZIP, preserving parent relationships."""
    execute(lambda settings, store: import_archive(store, archive))


@app.command("export")
def export(output: Path = Path("exports"), formats: str = "json,csv,xml,sql"):
    """Export all four data tables to archives in the requested directory."""
    execute(
        lambda settings, store: {
            "archives": export_data(
                store, output, list(dict.fromkeys(fmt.strip() for fmt in formats.split(",")))
            )
        }
    )


@app.command()
def validate():
    """Check referential integrity and report row counts."""
    execute(lambda settings, store: store.validate())


@app.command()
def preprocess():
    """Migrate/backfill SQLite scalar columns, with a backup before schema upgrades."""
    execute(lambda settings, store: store.preprocess(), initialize=False)


@app.command()
def enrich(raw_dir: Path | None = None, wait: bool = False):
    """Add vehicle metadata from cached HTML without fetching or changing engine specs."""
    from .enrichment import enrich_cached_pages

    execute(
        lambda settings, store: enrich_cached_pages(store, raw_dir or settings.data_dir / "raw"),
        wait=wait,
    )


@app.command("coverage")
def coverage():
    """Report vehicle categories, missing classification, metadata coverage and work state."""
    from .reporting import coverage_report

    execute(lambda settings, store: coverage_report(store))


@app.command("catalog")
def catalog(
    categories: str = "truck,bus,motorcycle",
    directory: Path | None = None,
    limit: int | None = typer.Option(None, min=1),
    refresh: bool = False,
):
    """Collect type-filtered NHTSA model listings; resume unfinished makes by default."""
    from .sources.nhtsa import NhtsaCatalog

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    target = directory or Settings().data_dir / "catalogs" / "nhtsa"
    target.mkdir(parents=True, exist_ok=True)
    collector = None
    try:
        with FileLock(str(target / "pipeline.lock"), timeout=0):
            collector = NhtsaCatalog(target)
            result = asyncio.run(
                collector.run(
                    list(dict.fromkeys(value.strip() for value in categories.split(","))),
                    limit,
                    refresh,
                )
            )
            typer.echo(json.dumps(result))
            if result["failed"]:
                raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if collector:
            collector.close()


@app.command("status")
def status():
    """Read progress across all vehicle sources while collectors are running."""
    from .reporting import (
        catalog_coverage,
        coverage_report,
        manufacturer_coverage,
        motorcycle_coverage,
    )

    settings = Settings()
    store = Store(settings.database_url)
    try:
        result = {
            "autoevolution": coverage_report(store),
            "nhtsa": catalog_coverage(settings.data_dir / "catalogs" / "nhtsa"),
            "motorcycles": motorcycle_coverage(settings.data_dir / "motorcycles"),
            "manufacturers": manufacturer_coverage(settings.data_dir / "manufacturers"),
        }
        typer.echo(json.dumps(result))
    except Exception as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        store.close()


@app.command("export-catalog")
def export_catalog_command(output: Path = Path("exports")):
    """Export car and NHTSA model listings together with source identity and detail level."""
    from .catalog_export import export_catalog

    settings = Settings()
    store = Store(settings.database_url)
    output.mkdir(parents=True, exist_ok=True)
    try:
        # This only reads databases; its own lock protects the output archive.
        with FileLock(str(output / ".catalog-export.lock"), timeout=0):
            typer.echo(
                json.dumps(
                    export_catalog(
                        store,
                        settings.data_dir / "catalogs" / "nhtsa",
                        output,
                        settings.data_dir / "motorcycles",
                        settings.data_dir / "manufacturers",
                    )
                )
            )
    except Exception as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        store.close()


@app.command("motorcycles")
def motorcycles(
    limit: int | None = typer.Option(None, min=1),
    refresh: bool = False,
    wait: bool = False,
):
    """Collect motorcycle/powersports generations and scalar specs; share the car network lock."""
    from .sources.motorcycles import collect_motorcycles

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    try:
        # The same source network lock prevents concurrent bulk car/motorcycle crawling.
        if wait:
            logging.info("motorcycle_waiting_for_source_lock")
        with FileLock(str(settings.data_dir / "pipeline.lock"), timeout=-1 if wait else 0):
            result = asyncio.run(
                collect_motorcycles(
                    settings,
                    settings.data_dir / "motorcycles",
                    limit,
                    refresh,
                )
            )
            typer.echo(json.dumps(result))
            if result["failed"]:
                raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("export-motorcycles")
def export_motorcycles(output: Path = Path("exports")):
    """Export all motorcycle specifications, photos and source-column definitions as JSON/CSV."""
    from .catalog_export import export_motorcycle_details

    output.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(output / ".motorcycle-export.lock"), timeout=0):
            typer.echo(
                json.dumps(export_motorcycle_details(Settings().data_dir / "motorcycles", output))
            )
    except Exception as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("manufacturers")
def manufacturers(refresh: bool = False):
    """Capture official specification tables for the supported truck and bus families."""
    from .sources.manufacturers import collect_manufacturers

    settings = Settings()
    target = settings.data_dir / "manufacturers"
    target.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(target / "pipeline.lock"), timeout=0):
            result = asyncio.run(collect_manufacturers(settings, target, refresh))
            typer.echo(json.dumps(result))
            if result["failed"]:
                raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("export-manufacturers")
def export_manufacturers(output: Path = Path("exports")):
    """Export official commercial-vehicle model identity and complete specification matrices."""
    from .catalog_export import export_manufacturer_details

    output.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(output / ".manufacturer-export.lock"), timeout=0):
            typer.echo(
                json.dumps(
                    export_manufacturer_details(
                        Settings().data_dir / "manufacturers",
                        output,
                    )
                )
            )
    except Exception as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
