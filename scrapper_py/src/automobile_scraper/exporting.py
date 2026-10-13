"""Stream exports into ZIPs; leave existing archives untouched until success."""

import csv
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from .storage import Automobile, AutomobilePhoto, Brand, Engine, Store

MODELS = (Brand, Automobile, Engine, AutomobilePhoto)


def value(value):
    return value.isoformat() if isinstance(value, datetime) else value


def sql_literal(item):
    if item is None:
        return "NULL"
    if isinstance(item, (int, float)):
        return str(item)
    if isinstance(item, (dict, list)):
        item = json.dumps(item, ensure_ascii=False)
    return "'" + str(value(item)).replace("'", "''") + "'"


def export_data(store: Store, output: Path, formats: list[str]) -> list[str]:
    if any(fmt not in {"json", "csv", "xml", "sql"} for fmt in formats):
        raise ValueError("Supported export formats: json, csv, xml, sql")
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        temp = Path(temporary)
        for fmt in formats:
            archive = temp / f"automobiles.{fmt}.zip"
            with ZipFile(archive, "w", ZIP_DEFLATED) as zipped, store.session() as session:
                for model in MODELS:
                    table = model.__tablename__
                    columns = [column.name for column in model.__table__.columns]
                    path = temp / f"{table}.{fmt}"
                    with path.open("w", encoding="utf-8", newline="") as file:
                        writer = None
                        if fmt == "json":
                            file.write("[")
                        elif fmt == "xml":
                            file.write(f'<?xml version="1.0" encoding="utf-8"?>\n<{table}>\n')
                        elif fmt == "csv":
                            writer = csv.DictWriter(file, fieldnames=columns)
                            writer.writeheader()
                        else:
                            file.write(
                                "-- Data-only SQL, import into the Python schema (init-db).\n"
                            )
                        first = True
                        for row in session.scalars(
                            select(model).order_by(model.id).execution_options(yield_per=500)
                        ):
                            data = {key: value(getattr(row, key)) for key in columns}
                            if fmt == "json":
                                file.write(
                                    ("" if first else ",") + json.dumps(data, ensure_ascii=False)
                                )
                            elif fmt == "csv":
                                writer.writerow(
                                    {
                                        key: json.dumps(item, ensure_ascii=False)
                                        if isinstance(item, (dict, list))
                                        else item
                                        for key, item in data.items()
                                    }
                                )
                            elif fmt == "xml":
                                node = ET.Element(
                                    "photo" if table == "automobile_photos" else table[:-1]
                                )
                                for key, item in data.items():
                                    ET.SubElement(node, key).text = (
                                        json.dumps(item, ensure_ascii=False)
                                        if isinstance(item, (dict, list))
                                        else str(item)
                                        if item is not None
                                        else ""
                                    )
                                file.write(ET.tostring(node, encoding="unicode") + "\n")
                            else:
                                names = ",".join('"' + key + '"' for key in columns)
                                values = ",".join(sql_literal(data[key]) for key in columns)
                                file.write(f'INSERT INTO "{table}" ({names}) VALUES ({values});\n')
                            first = False
                        if fmt == "json":
                            file.write("]")
                        elif fmt == "xml":
                            file.write(f"</{table}>\n")
                    zipped.write(path, path.name)
            target = output / archive.name
            os.replace(archive, target)
            paths.append(str(target))
    return paths
