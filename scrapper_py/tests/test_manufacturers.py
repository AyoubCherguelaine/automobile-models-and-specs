import pytest

from automobile_scraper.parsers import ParseError
from automobile_scraper.sources.manufacturers import ManufacturerStore, specification_tables

SOURCE = ("maker", "Maker", "Model", "truck", "heavy_truck", "https://example.com/model")
HTML = """<title>Model specifications</title><h2>Engines</h2><table>
<tr><th rowspan="2">Engine</th><th colspan="2">Output</th></tr>
<tr><th>kW</th><th>Nm</th></tr>
<tr><td>D13</td><td>309</td><td>2100</td></tr></table>"""


def test_manufacturer_tables_preserve_variant_column_positions():
    title, tables = specification_tables(HTML, "Model")
    assert title == "Model specifications"
    position, heading, cells = tables[0]
    assert position == 0 and heading == "Engines"
    assert cells[:4] == [
        (0, 0, 2, 1, True, "Engine"),
        (0, 1, 1, 2, True, "Output"),
        (1, 1, 1, 1, True, "kW"),
        (1, 2, 1, 1, True, "Nm"),
    ]
    assert cells[-1] == (2, 2, 1, 1, False, "2100")


def test_error_page_refresh_preserves_prior_specs(tmp_path):
    store = ManufacturerStore(tmp_path)
    try:
        for _ in range(2):
            assert store.save(SOURCE, HTML)["cells"] == 7
        assert store.connection.execute("SELECT count(*) FROM models").fetchone()[0] == 1
        with pytest.raises(ParseError, match="requested model"):
            store.save(SOURCE, "<title>Sorry</title><table><tr><td>Error</td></tr></table>")
        assert (
            store.connection.execute("SELECT count(*) FROM specification_cells").fetchone()[0] == 7
        )
        assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()


def test_manufacturer_export_preserves_every_cell(tmp_path):
    import csv
    import io
    import json
    from zipfile import ZipFile

    from automobile_scraper.catalog_export import export_manufacturer_details

    store = ManufacturerStore(tmp_path)
    store.save(SOURCE, HTML)
    store.close()
    result = export_manufacturer_details(tmp_path, tmp_path / "exports")
    assert result["counts"] == {
        "models": 1,
        "specification_tables": 1,
        "specification_cells": 7,
        "specification_context": 1,
        "powertrain_specs": 0,
        "bus_variant_fields": 0,
        "bus_variant_specs": 0,
    }
    with ZipFile(result["archive"]) as archive:
        rows = json.loads(archive.read("specification_cells.json"))
        csv_rows = list(
            csv.DictReader(io.StringIO(archive.read("specification_cells.csv").decode()))
        )
        assert len(rows) == len(csv_rows) == 7
        assert [(row["row_position"], row["column_position"], row["value"]) for row in rows] == [
            (0, 0, "Engine"),
            (0, 1, "Output"),
            (1, 1, "kW"),
            (1, 2, "Nm"),
            (2, 0, "D13"),
            (2, 1, "309"),
            (2, 2, "2100"),
        ]


def test_manufacturer_coverage_reads_existing_data_without_writer_lock(tmp_path):
    from automobile_scraper.reporting import manufacturer_coverage

    assert manufacturer_coverage(tmp_path)["status"] == "not_collected"
    assert not (tmp_path / "specifications.db").exists()
    store = ManufacturerStore(tmp_path)
    try:
        store.connection.execute("INSERT INTO work(source_url) VALUES(?)", (SOURCE[-1],))
        store.connection.commit()
        store.save(SOURCE, HTML)
        report = manufacturer_coverage(tmp_path)
        assert report["models"] == 1
        assert report["specification_tables"] == 1
        assert report["specification_cells"] == 7
        assert report["categories"] == {"truck": 1}
        assert report["work_status"] == {"completed": 1}
        assert report["detail_level"] == "specification_tables"
    finally:
        store.close()


@pytest.fixture
def manufacturer_transport(tmp_path, monkeypatch):
    import httpx

    from automobile_scraper.config import Settings
    from automobile_scraper.fetching import Fetcher
    from automobile_scraper.sources import manufacturers

    sources = [
        (*SOURCE[:-1], url)
        for url in (
            "https://example.com/first",
            "https://example.com/second",
            "https://another.example/model",
        )
    ]
    monkeypatch.setattr(manufacturers, "SOURCES", sources)
    responses = {}
    sessions = []

    class TestFetcher(Fetcher):
        async def __aenter__(self):
            await self.client.aclose()
            self.client = httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(responses.get(str(request.url), 200), text=HTML)
                )
            )
            sessions.append(self)
            return self

    monkeypatch.setattr("automobile_scraper.fetching.Fetcher", TestFetcher)
    settings = Settings(
        data_dir=tmp_path,
        request_interval=0,
        respect_robots=False,
        _env_file=None,
    )
    return settings, sources, responses, sessions


async def test_manufacturer_collection_reuses_host_sessions_and_skips_completed(
    tmp_path, manufacturer_transport
):
    from automobile_scraper.sources.manufacturers import collect_manufacturers

    settings, sources, _, sessions = manufacturer_transport
    result = await collect_manufacturers(settings, tmp_path)
    assert result["succeeded"] == result["requests"] == len(sources)
    assert result["work_status"] == {"completed": 3}
    assert len(sessions) == 2
    assert sorted(session.request_count for session in sessions) == [1, 2]
    assert all(session.client.is_closed for session in sessions)
    repeated = await collect_manufacturers(settings, tmp_path)
    assert repeated["attempted"] == repeated["requests"] == 0
    assert repeated["skipped"] == 3
    assert len(sessions) == 2


async def test_manufacturer_denied_refresh_keeps_specs_and_remaining_jobs(
    tmp_path, manufacturer_transport
):
    from automobile_scraper.sources.manufacturers import collect_manufacturers

    settings, sources, responses, sessions = manufacturer_transport
    await collect_manufacturers(settings, tmp_path)
    responses[sources[0][-1]] = 403
    result = await collect_manufacturers(settings, tmp_path, refresh=True)
    assert result["stopped_reason"] == "authorization_failure"
    assert result["attempted"] == result["failed"] == result["requests"] == 1
    assert result["failures"][0]["source_url"] == sources[0][-1]
    assert result["work_status"] == {"completed": 2, "failed": 1}
    assert all(session.client.is_closed for session in sessions)
    store = ManufacturerStore(tmp_path)
    try:
        assert store.connection.execute("SELECT count(*) FROM models").fetchone()[0] == 3
        assert (
            store.connection.execute("SELECT count(*) FROM specification_cells").fetchone()[0] == 21
        )
    finally:
        store.close()


def test_powertrain_view_preserves_options_and_excludes_unit_rows(tmp_path):
    from automobile_scraper.catalog_export import export_manufacturer_details

    source = ("scania", "Scania", "R-series", "truck", "heavy_truck", SOURCE[-1])
    html = """<title>R-series specifications</title><h3>16-litre</h3><table>
    <tr><td>Engine ID</td><td>Output</td><td>Torque</td><td>Fuel</td></tr>
    <tr><td>DC16 123</td><td>770 Hp</td><td>3700 Nm</td><td>Diesel/HVO</td></tr>
    <tr><td>DC16 122</td><td>660 Hp</td><td>3300 Nm</td><td>Diesel/HVO</td></tr>
    </table><h3>Electric</h3><table>
    <tr><th>Propulsion Power</th><th>Regeration Power</th><th>Gearbox</th>
    <th>Installed battery capacity</th><th>Charging power</th><th>PTO options</th></tr>
    <tr><td>(Continuous)</td><td>(Peak)</td><td>No of speed</td>
    <td>kWh</td><td>kW Max</td><td>Type *</td></tr>
    <tr><td>210kW</td><td>294kW</td><td>2</td><td>356/445</td><td>375/750</td><td>EL/EM</td></tr>
    </table>"""
    store = ManufacturerStore(tmp_path)
    try:
        store.save(source, html)
        engines = store.connection.execute(
            "SELECT engine_code,output,torque,fuel FROM powertrain_specs "
            "WHERE output IS NOT NULL ORDER BY row_position"
        ).fetchall()
        assert engines == [
            ("DC16 123", "770 Hp", "3700 Nm", "Diesel/HVO"),
            ("DC16 122", "660 Hp", "3300 Nm", "Diesel/HVO"),
        ]
        electric = store.connection.execute(
            "SELECT engine_code,propulsion_power,regeneration_power,gearbox,"
            "installed_battery_capacity,charging_power,pto_options "
            "FROM powertrain_specs WHERE propulsion_power IS NOT NULL"
        ).fetchall()
        assert electric == [(None, "210kW", "294kW", "2", "356/445", "375/750", "EL/EM")]
        exported = export_manufacturer_details(tmp_path, tmp_path / "exports")
        assert exported["counts"]["powertrain_specs"] == 3
        # A successful refresh removes options absent from the new table.
        store.save(
            source,
            html.replace(
                "<tr><td>DC16 122</td><td>660 Hp</td><td>3300 Nm</td><td>Diesel/HVO</td></tr>", ""
            ),
        )
        assert store.connection.execute("SELECT count(*) FROM powertrain_specs").fetchone()[0] == 2
    finally:
        store.close()


@pytest.mark.parametrize("manufacturer", ["mercedes_benz", "setra"])
def test_bus_views_follow_variant_columns_and_keep_alternative_engines(tmp_path, manufacturer):
    source = (manufacturer, "Example", "Citaro", "bus", "city_bus", SOURCE[-1])
    html = """<title>Citaro</title><h2>Specifications</h2>
    <table><tr><th></th><th>Citaro</th><th>Citaro G</th></tr>
    <tr><td colspan="3">Dimensions</td></tr>
    <tr><td>Length [mm]</td><td>12.135</td><td>18.125</td></tr>
    <tr><td>Width [mm]</td><td colspan="2">2.550</td></tr></table>
    <table><tr><th></th><th>Citaro G</th><th>Citaro</th></tr>
    <tr><td colspan="3">Transmission</td></tr>
    <tr><td>Engine</td><td>OM 470 / OM 936</td><td>OM 936</td></tr>
    <tr><td>Output [kW]</td><td>265 / 220</td><td>220</td></tr>
    <tr><td>Engine 2*</td><td>OM 936</td><td>OM 936</td></tr>
    <tr><td>Output 2* [kW]</td><td>260</td><td>260</td></tr></table>"""
    store = ManufacturerStore(tmp_path)
    try:
        store.save(source, html)
        assert store.connection.execute(
            "SELECT variant_name,length_mm_text,width_mm_text,engine,output_kw_text,"
            "engine_2,output_2_kw_text FROM bus_variant_specs ORDER BY variant_name"
        ).fetchall() == [
            ("Citaro", "12.135", "2.550", "OM 936", "220", "OM 936", "260"),
            ("Citaro G", "18.125", "2.550", "OM 470 / OM 936", "265 / 220", "OM 936", "260"),
        ]
        assert store.connection.execute(
            "SELECT variant_name,source_section,table_position,row_position,column_position,"
            "variant_column_position FROM bus_variant_fields WHERE field_name='Width [mm]' "
            "ORDER BY variant_name"
        ).fetchall() == [
            ("Citaro", "Dimensions", 0, 3, 1, 1),
            ("Citaro G", "Dimensions", 0, 3, 1, 2),
        ]
    finally:
        store.close()


def test_bus_conflicting_values_stay_in_evidence_without_choosing_a_value(tmp_path):
    source = ("mercedes_benz", "Mercedes-Benz", "Citaro", "bus", "city_bus", SOURCE[-1])
    table = """<table><tr><th></th><th>Citaro</th></tr>
    <tr><td>Length [mm]</td><td>12.135</td></tr></table>"""
    store = ManufacturerStore(tmp_path)
    try:
        store.save(source, "<title>Citaro</title>" + table + table.replace("12.135", "12.170"))
        assert store.connection.execute(
            "SELECT length_mm_text FROM bus_variant_specs"
        ).fetchone() == (None,)
        assert store.connection.execute(
            "SELECT value FROM bus_variant_fields WHERE field_name='Length [mm]' "
            "ORDER BY table_position"
        ).fetchall() == [("12.135",), ("12.170",)]
    finally:
        store.close()


def test_truck_powertrains_keep_explicit_tab_context(tmp_path):
    from automobile_scraper.sources.manufacturers import specification_contexts

    html = """<title>Model</title>
    <button id="euro6" role="tab">Euro 6</button>
    <div role="tabpanel" aria-labelledby="euro6"><h3>13-litre</h3>
    <table><tr><th>Output</th><th>Torque</th><th>Fuel</th></tr>
    <tr><td>420 Hp</td><td>2300 Nm</td><td>Diesel/HVO</td></tr></table></div>
    <div role="tabpanel" aria-labelledby="missing"><table>
    <tr><td>Unknown</td></tr></table></div>"""
    assert specification_contexts(html) == {0: "Euro 6", 1: None}
    source = ("scania", *SOURCE[1:])
    store = ManufacturerStore(tmp_path)
    try:
        store.save(source, html)
        assert store.connection.execute(
            "SELECT output,source_heading,source_tab FROM powertrain_specs"
        ).fetchone() == ("420 Hp", "13-litre", "Euro 6")
        # Context is replaced atomically along with the refreshed source matrix.
        store.save(source, html.replace("Euro 6", "Euro 5"))
        assert store.connection.execute("SELECT source_tab FROM powertrain_specs").fetchone() == (
            "Euro 5",
        )
        assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()


def test_cached_context_requires_matching_matrix_hash_and_keeps_model_timestamp(tmp_path):
    import json

    source = ("scania", *SOURCE[1:])
    html = (
        '<title>Model</title><b id="tab">Euro 6</b><div role="tabpanel" '
        'aria-labelledby="tab">' + HTML + "</div>"
    )
    store = ManufacturerStore(tmp_path)
    try:
        store.save(source, html)
        initial = store.connection.execute("SELECT * FROM models").fetchall()
        store.connection.execute("DELETE FROM specification_context")
        store.connection.commit()
        raw = tmp_path / "raw" / "run"
        raw.mkdir(parents=True)
        metadata = json.dumps({"url": SOURCE[-1], "method": "GET", "status": 200})
        (raw / "0broken.json").write_text("bad json")
        (raw / "1stale.json").write_text(metadata)
        (raw / "1stale.html").write_text(html.replace("Euro 6", "Euro 5"))
        missing = store.enrich_cached_context(tmp_path / "raw")
        assert missing["models_updated"] == 0
        assert missing["models_without_matching_capture"] == 1
        (raw / "2matching.json").write_text(metadata)
        (raw / "2matching.html").write_text(html)
        result = store.enrich_cached_context(tmp_path / "raw")
        assert result["models_updated"] == result["invalid_captures"] == 1
        assert result["models_without_matching_capture"] == 0
        assert store.connection.execute(
            "SELECT tab_label FROM specification_context"
        ).fetchone() == ("Euro 6",)
        assert store.connection.execute("SELECT * FROM models").fetchall() == initial
        assert store.enrich_cached_context(tmp_path / "raw")["models_updated"] == 0
    finally:
        store.close()


def test_existing_powertrain_view_is_upgraded_without_rewriting_source_data(tmp_path):
    store = ManufacturerStore(tmp_path)
    store.save(SOURCE, HTML)
    cells = store.connection.execute("SELECT * FROM specification_cells").fetchall()
    with store.connection:
        store.connection.execute("DROP VIEW powertrain_specs")
        store.connection.execute("CREATE VIEW powertrain_specs AS SELECT * FROM models")
    store.close()
    migrated = ManufacturerStore(tmp_path)
    try:
        assert "source_tab" in {
            row[1] for row in migrated.connection.execute("PRAGMA table_info(powertrain_specs)")
        }
        assert migrated.connection.execute("SELECT * FROM specification_cells").fetchall() == cells
    finally:
        migrated.close()


def test_setra_dimensions_keep_approximate_values_and_nonbreaking_label_spaces(tmp_path):
    source = ("setra", "Setra", "TopClass", "bus", "coach", SOURCE[-1])
    html = """<title>TopClass</title><table><tr><th></th><th>S 515 HDH</th></tr>
    <tr><td>Height incl. air-conditioning system&nbsp;[mm]</td><td>approx. 3,880</td></tr>
    <tr><td>Gross vehicle weight [kg]</td><td>24.750</td></tr></table>"""
    store = ManufacturerStore(tmp_path)
    try:
        store.save(source, html)
        assert store.connection.execute(
            "SELECT variant_name,height_with_ac_mm_text,gross_vehicle_weight_kg_text "
            "FROM bus_variant_specs"
        ).fetchone() == ("S 515 HDH", "approx. 3,880", "24.750")
    finally:
        store.close()
