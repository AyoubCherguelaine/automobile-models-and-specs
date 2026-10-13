import pytest

from automobile_scraper.config import Settings
from automobile_scraper.parsers import ParseError
from automobile_scraper.sources.motorcycles import (
    MotorcycleStore,
    collect_motorcycles,
    discover_motorcycle_urls,
    parse_motorcycle,
)

BASE = "https://example.com"
URL = BASE + "/moto/example-tourer-2020.html"
DETAIL = """<h1 class="newstitle">Example Tourer 2020-2021 Photos</h1>
<span itemprop="itemListElement">Home</span><span itemprop="itemListElement">Moto</span>
<span itemprop="itemListElement">Example</span>
<p class="nomgtop"><b>Production years:</b> 2020, 2021<br><b>Segment:</b> Classic<br></p>
<script id="schema-gallery-data" type="application/ld+json">
{"associatedMedia":[{"contentUrl":"/photo.jpg"}]}</script>
<div class="enginedata engine-inline"><table class="techdata">
<tr><th class="title">engine specs</th></tr>
<tr><td>Displacement:</td><td>247 cm3</td></tr>
<tr><td>Horsepower:</td><td>12 KW @ 5600 RPM<br>16 HP @ 5600 RPM</td></tr>
<tr><td>Fuel System:</td><td>Bing carburetors</td></tr>
<tr><td>Compression Ratio:</td><td>5.75:1</td></tr></table>
<table class="techdata"><tr><th class="title">dimensions</th></tr>
<tr><td>Weight:</td><td>298 lbs OR 135 kg</td></tr></table></div>"""
DIRECTORY = """<div class="carman"><meta itemprop="url" content="/moto/example/">
<h2 itemprop="name">Example</h2></div>"""
BRAND = """<div class="carmod"><div class="padcol2">
<a href="/moto/example/tourer/">Tourer</a></div></div>"""
FAMILY = f'''<div class="carmodel"><div class="years"><h2><a href="{URL}">Tourer</a>
</h2></div></div>'''


def test_motorcycle_technical_values_are_scalar_and_source_fields_are_preserved(tmp_path):
    record, fields, registry = parse_motorcycle(DETAIL, URL)
    assert record.information["segment"] == "Classic"
    assert record.information["vehicle_category"] == "motorcycle"
    assert fields["displacement_cc"] == 247 and fields["power_kw"] == 12
    assert fields["unladen_weight_kg"] == 135
    assert fields["spec_engine_specs_compression_ratio"] == "5.75:1"
    assert fields["cylinders"] is None
    store = MotorcycleStore(tmp_path)
    try:
        for _ in range(2):
            store.save(record, fields, registry)
        assert store.connection.execute("SELECT count(*) FROM vehicles").fetchone()[0] == 1
        assert store.connection.execute("SELECT count(*) FROM photos").fetchone()[0] == 1
        assert store.connection.execute(
            "SELECT year,year_end,power_kw FROM vehicles"
        ).fetchone() == (
            2020,
            2021,
            12,
        )
        assert store.connection.execute("SELECT count(*) FROM spec_columns").fetchone()[0] == 5
        assert store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        import json
        from zipfile import ZipFile

        from automobile_scraper.catalog_export import export_motorcycle_details

        result = export_motorcycle_details(tmp_path, tmp_path / "exports")
        with ZipFile(result["archive"]) as archive:
            values = json.loads(archive.read("vehicles.json"))
            assert values[0]["spec_engine_specs_compression_ratio"] == "5.75:1"
            assert values[0]["power_kw"] == 12
            assert len(json.loads(archive.read("photos.json"))) == 1
        assert result["counts"] == {"vehicles": 1, "photos": 1, "spec_columns": 5}
    finally:
        store.close()


def test_powersports_segment_is_not_mislabeled_as_motorcycle():
    record, _, _ = parse_motorcycle(DETAIL.replace("Classic", "ATV"), URL)
    assert record.information["vehicle_category"] == "atv"
    record, _, _ = parse_motorcycle(DETAIL.replace("Classic", "Unrecognized"), URL)
    assert record.information["vehicle_category"] == "unknown"


def test_motorcycle_type_normalizes_explicit_cylinders_and_keeps_source_text():
    source = DETAIL.replace(
        "<tr><td>Displacement:</td>",
        "<tr><td>Type:</td><td>air-cooled, two-stroke, twin cylinder</td></tr>"
        "<tr><td>Displacement:</td>",
    )
    _, fields, _ = parse_motorcycle(source, URL)
    assert fields["cylinders"] == 2
    assert fields["engine_type"] is None  # No inline/V layout is stated.
    assert (
        fields["cylinders_raw"]
        == fields["spec_engine_specs_type"]
        == ("air-cooled, two-stroke, twin cylinder")
    )


def test_source_column_collision_rolls_back_schema_and_previous_values(tmp_path):
    record, fields, registry = parse_motorcycle(DETAIL, URL)
    store = MotorcycleStore(tmp_path)
    try:
        store.save(record, fields, registry)
        conflicting = {
            "spec_new_field": ("new", "field"),
            "spec_engine_specs_fuel_system": ("engine specs", "fuel-system"),
        }
        with pytest.raises(ParseError, match="collision"):
            store.save(record, {**fields, "spec_new_field": "new"}, conflicting)
        columns = {row[1] for row in store.connection.execute("PRAGMA table_info(vehicles)")}
        assert "spec_new_field" not in columns
        assert store.connection.execute("SELECT power_kw FROM vehicles").fetchone()[0] == 12
        assert store.connection.execute("SELECT count(*) FROM photos").fetchone()[0] == 1
    finally:
        store.close()


def test_discovery_excludes_unrelated_links_and_deduplicates_generations():
    assert discover_motorcycle_urls(DIRECTORY, BASE + "/moto/", "directory") == [
        (BASE + "/moto/example/", "brand")
    ]
    assert discover_motorcycle_urls(BRAND, BASE + "/moto/example/", "brand") == [
        (BASE + "/moto/example/tourer/", "family")
    ]
    assert discover_motorcycle_urls(FAMILY + FAMILY, BASE + "/moto/example/tourer/", "family") == [
        (URL, "detail")
    ]
    with pytest.raises(ParseError, match="No motorcycle"):
        discover_motorcycle_urls('<a href="/news/example.html">News</a>', BASE, "family")


@pytest.mark.asyncio
async def test_motorcycle_pipeline_resumes_without_duplicate_records(tmp_path):
    pages = {
        BASE + "/moto/": DIRECTORY,
        BASE + "/moto/example/": BRAND,
        BASE + "/moto/example/tourer/": FAMILY,
        URL: DETAIL,
    }

    class CachedFetcher:
        async def request(self, url):
            return pages[url]

    settings = Settings(base_url=BASE, data_dir=tmp_path)
    first = await collect_motorcycles(settings, tmp_path / "moto", fetcher=CachedFetcher())
    assert first["attempted"] == 4 and first["models"] == 1 and first["failed"] == 0
    second = await collect_motorcycles(settings, tmp_path / "moto", fetcher=CachedFetcher())
    assert second["attempted"] == 1 and second["models"] == 1
    assert second["work_status"] == {"completed": 4}
