import json

import httpx
import pytest

from automobile_scraper.sources.nhtsa import NhtsaCatalog


def reply(rows):
    return httpx.Response(200, json={"Count": len(rows), "Results": rows})


def make():
    return {"MakeId": 450, "MakeName": "FREIGHTLINER", "VehicleTypeId": 5}


def model(model_id=123):
    return {
        "Make_ID": 450,
        "Make_Name": "FREIGHTLINER",
        "Model_ID": model_id,
        "Model_Name": "Bus",
        "VehicleTypeId": 5,
        "VehicleTypeName": "Bus",
    }


@pytest.mark.asyncio
async def test_catalog_resume_and_refresh_preserve_type_identity(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return reply([make()] if "GetMakes" in request.url.path else [model()])

    collector = NhtsaCatalog(tmp_path, interval=0)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert (await collector.run(["bus"], client=client))["succeeded"] == 1
            collector.run_id += "-resume"
            assert (await collector.run(["bus"], client=client))["attempted"] == 0
            collector.run_id += "-refresh"
            assert (await collector.run(["bus"], refresh=True, client=client))["succeeded"] == 1
        assert len([x for x in calls if "GetModels" in x]) == 2
        assert collector.coverage()["categories"] == {"bus": 1}
        row = collector.connection.execute(
            "SELECT source,make_id,model_id,vehicle_category,detail_level FROM vehicles"
        ).fetchone()
        assert row == ("nhtsa_vpic", 450, 123, "bus", "model_listing")
        raw = list((tmp_path / "raw").glob("*/*.json"))
        assert raw and json.loads(raw[0].read_text())["status"] == 200
    finally:
        collector.close()


def test_wrong_model_type_does_not_replace_previous_data(tmp_path):
    collector = NhtsaCatalog(tmp_path)
    try:
        collector.stage_makes([make()], "bus")
        collector.save_models([model()], 450, "bus", "https://example.com/")
        invalid = {**model(456), "VehicleTypeId": 3}
        with pytest.raises(ValueError, match="identity/type"):
            collector.save_models([model(789), invalid], 450, "bus", "https://example.com/")
        assert collector.connection.execute("SELECT model_id FROM vehicles").fetchall() == [(123,)]
    finally:
        collector.close()


@pytest.mark.asyncio
async def test_failed_make_retries_without_losing_saved_models(tmp_path):
    def denied(request):
        if "GetMakes" in request.url.path:
            return reply([make()])
        return httpx.Response(403)

    collector = NhtsaCatalog(tmp_path, interval=0)
    try:
        collector.stage_makes([make()], "bus")
        collector.save_models([model()], 450, "bus", "https://example.com/")
        async with httpx.AsyncClient(transport=httpx.MockTransport(denied)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await collector.run(["bus"], refresh=True, client=client)
        assert collector.coverage()["model_listings"] == 1
        assert collector.coverage()["work_status"] == {"failed": 1}
        assert collector.connection.execute("SELECT status,failed FROM runs").fetchone() == (
            "interrupted",
            1,
        )
    finally:
        collector.close()


def test_combined_catalog_export_preserves_sources_and_unknown_years(store, tmp_path):
    import csv
    import io
    from zipfile import ZipFile

    from automobile_scraper.catalog_export import export_catalog
    from automobile_scraper.domain import AutomobileData, BrandData
    from automobile_scraper.reporting import catalog_coverage

    with store.session() as session, session.begin():
        store.save_brand(session, BrandData(name="Example", url="https://example.com/"))
        store.save_automobile(
            session,
            AutomobileData(
                name="Example Sedan 2020",
                brand_name="Example",
                url="https://example.com/car.html",
            ),
        )
    collector = NhtsaCatalog(tmp_path / "catalog")
    try:
        collector.stage_makes([make()], "bus")
        collector.save_models([model()], 450, "bus", "https://example.com/api")
    finally:
        collector.close()
    assert catalog_coverage(tmp_path / "catalog")["categories"] == {"bus": 1}
    result = export_catalog(store, tmp_path / "catalog", tmp_path / "exports")
    assert result["counts"] == {"autoevolution": 1, "nhtsa_vpic": 1}
    with ZipFile(result["archive"]) as archive:
        rows = json.loads(archive.read("vehicles.json"))
        csv_rows = list(csv.DictReader(io.StringIO(archive.read("vehicles.csv").decode())))
    assert len(rows) == len(csv_rows) == 2
    assert rows[0]["vehicle_category"] == "car" and rows[0]["year"] == 2020
    assert rows[0]["detail_level"] == "model_listing"  # No engine data supplied.
    assert rows[1]["source_id"] == "450:123:5" and rows[1]["year"] is None
    assert rows[1]["vehicle_category"] == "bus" and rows[1]["detail_level"] == "model_listing"
