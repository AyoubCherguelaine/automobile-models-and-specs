import json

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import select, text

from automobile_scraper.domain import AutomobileData, BrandData
from automobile_scraper.enrichment import enrich_cached_pages
from automobile_scraper.preprocessing import preprocess_engine
from automobile_scraper.storage import Automobile
from automobile_scraper.vehicle_information import (
    clean_model_name,
    extract_vehicle_information,
    title_information,
)

HTML = """<div><h1 class="newstitle">2025 Example Family</h1>
<span itemprop="itemListElement">Home</span><span itemprop="itemListElement">Cars</span>
<span itemprop="itemListElement">Example</span>
<span itemprop="itemListElement"><a href="/family/">Family</a></span>
<span itemprop="itemListElement">2025 Example Family</span>
</div><div><table><tr><td><b>Body style:</b></td><td>Wagon (station wagon, estate)</td></tr>
<tr><td><b>Trim:</b></td><td>Luxury</td></tr>
<tr><td><b>Segment:</b></td><td>Medium</td></tr>
<tr><td><b>Production years:</b></td><td>2025, 2026</td></tr>
<tr><td><b>Assembled at:</b></td><td>Sweden</td></tr>
<tr><td><b>Infotainment:</b></td><td>Apple CarPlay, Android Auto</td></tr>
<tr><td><b>Autonomous driving:</b></td><td>Level 2 assistance</td></tr></table>
<script type="application/ld+json">{"@type":"Car","bodyType":"Coupé (two-door)",
"category":"Compact","numberOfDoors":5,"vehicleSeatingCapacity":5,"image":"/car.jpg",
"vehicleGeneration":"Second generation","vehicleConfiguration":"5dr wagon Luxury"}</script></div>"""
URL = "https://example.com/cars/family-2025.html"


def test_explicit_metadata_precedes_schema_and_preserves_source_details():
    info = extract_vehicle_information(
        BeautifulSoup(HTML, "lxml"), "2025 Example Family", "Example", URL
    )
    assert info["model_name"] == "Family" and info["model_family"] == "Family"
    assert info["body_style"] == "Wagon (station wagon, estate)"
    assert info["body_type"] == "Wagon" and info["body_style_source"] == "page_table"
    assert info["segment"] == "Medium"
    assert info["doors"] == 5 and info["seats"] == 5
    assert info["generation"] == "Second generation" and info["trim"] == "Luxury"
    assert info["vehicle_configuration"] == "5dr wagon Luxury"
    assert info["production_years_text"] == "2025, 2026"
    assert info["assembly_location"] == "Sweden"
    assert info["supports_apple_carplay"] is True and info["supports_android_auto"] is True
    assert info["autonomy_level"] == 2
    assert info["main_image_url"] == "https://example.com/car.jpg"


def test_schema_graph_and_unknown_physical_details_are_not_guessed():
    source = (
        '<h1 class="newstitle">Example Coupe</h1><script type="application/ld+json">'
        + json.dumps(
            {"@graph": [{"@type": "Car", "bodyType": "Coupé (two-door)", "category": "Medium"}]}
        )
        + "</script>"
    )
    info = extract_vehicle_information(
        BeautifulSoup(source, "lxml"), "Example Coupe", "Example", URL
    )
    assert info["body_type"] == "Coupe" and info["body_style_source"] == "structured_data"
    assert info["doors"] is None and info["seats"] is None
    assert info["supports_android_auto"] is None
    assert info["generation"] is None and info["trim"] is None


@pytest.mark.parametrize(
    "name,brand,expected",
    [
        ("AC  Aceca 1998-2000 Photos, engines &amp; full specs", "AC", "Aceca"),
        ("2024 Acura Integra Type S Photos, engines & full specs", "Acura", "Integra Type S"),
        ("PEUGEOT 2008 Photos", "PEUGEOT", "2008"),
        ("LAND ROVER Defender (2020-Present) Photos", "LAND ROVER", "Defender"),
    ],
)
def test_clean_model_identity(name, brand, expected):
    assert clean_model_name(name, brand) == expected


def test_title_body_and_generation_only_use_explicit_designations():
    assert (
        title_information("AC 428 Convertible 1966-1971 Photos", "AC")["body_type"] == "Convertible"
    )
    assert title_information("AC Cobra MkIII 1965-1967 Photos", "AC")["generation"] == "MkIII"
    assert "body_type" not in title_information("PORSCHE 911 Touring", "PORSCHE")


@pytest.mark.parametrize(
    "fuel,system,gearbox,drive,expected",
    [
        (
            "Gasoline",
            "Turbocharged Direct Injection",
            "7-Speed DCT",
            "All Wheel Drive",
            ("Combustion", "Gasoline", "Turbocharged", "Direct injection", "Dual-clutch", "AWD"),
        ),
        (
            "Mild Hybrid Diesel",
            "Turbocharged Common Rail",
            "8-Speed Automatic",
            "Rear Wheel Drive",
            ("Mild hybrid", "Diesel", "Turbocharged", "Common rail", "Automatic", "RWD"),
        ),
        (
            "Plug-in Hybrid",
            "Supercharged Multipoint Injection",
            "CVT",
            "Front Wheel Drive",
            ("Plug-in hybrid", None, "Supercharged", "Multipoint injection", "CVT", "FWD"),
        ),
        (
            "Electric",
            "Electric",
            "Single-Speed",
            "Four Wheel Drive",
            ("Electric", "Electric", None, None, "Single-speed", "4WD"),
        ),
    ],
)
def test_engine_and_transmission_classifications(fuel, system, gearbox, drive, expected):
    values = preprocess_engine(
        {
            "Engine Specs": {"Fuel:": fuel, "Fuel System:": system},
            "Transmission Specs": {"Gearbox:": gearbox, "Drive Type:": drive},
        },
        "Engine",
    )
    assert (
        tuple(
            values[column]
            for column in (
                "powertrain_type",
                "fuel_type",
                "aspiration",
                "injection_type",
                "transmission_type",
                "drive_layout",
            )
        )
        == expected
    )
    assert values["engine_code"] is None


def test_unknown_aspiration_and_explicit_motor_count():
    values = preprocess_engine({"Engine Specs": {"Fuel:": "Electric"}}, "Dual Motor AWD")
    assert values["electric_motor_count"] == 2
    assert values["aspiration"] is None
    values = preprocess_engine({"Engine Specs": {"Fuel System:": "Multipoint Injection"}}, "1.6")
    assert values["aspiration"] is None  # Absence of turbo does not prove naturally aspirated.


def test_cached_enrichment_is_idempotent_and_preserves_richer_info_on_legacy_import(
    store, tmp_path
):
    data = AutomobileData(url=URL, name="Example Family 2025", brand_name="Example")
    with store.session() as session, session.begin():
        store.save_brand(session, BrandData(name="Example", url="https://example.com/"))
        store.save_automobile(session, data)
    directory = tmp_path / "raw" / "run"
    directory.mkdir(parents=True)
    (directory / "page.html").write_text(HTML)
    (directory / "page.json").write_text(
        json.dumps({"url": URL, "method": "GET", "status": 200, "captured_at": 1})
    )
    for _ in range(2):
        result = enrich_cached_pages(store, tmp_path / "raw")
        assert result["updated_models"] == 1 and result["failed"] == 0
    with store.session() as session, session.begin():
        store.save_automobile(session, data)
    with store.session() as session:
        row = session.scalar(select(Automobile))
        assert (
            row.body_type == "Wagon" and row.doors == 5 and row.metadata_source == "page_metadata"
        )
        assert session.execute(text("SELECT body_type,model_family FROM vehicle_specs")).all() == []
    assert store.counts()["automobiles"] == 1


def test_cached_page_without_body_metadata_keeps_stored_classification(store, tmp_path):
    data = AutomobileData(
        url=URL,
        name="Example Family 2025",
        brand_name="Example",
        information={
            "body_style": "Wagon",
            "body_type": "Wagon",
            "body_style_source": "page_table",
        },
    )
    with store.session() as session, session.begin():
        store.save_brand(session, BrandData(name="Example", url="https://example.com/"))
        store.save_automobile(session, data)
    directory = tmp_path / "raw" / "run"
    directory.mkdir(parents=True)
    (directory / "page.html").write_text('<h1 class="newstitle">2025 Example Family</h1>')
    (directory / "page.json").write_text(
        json.dumps({"url": URL, "method": "GET", "status": 200, "captured_at": 1})
    )
    result = enrich_cached_pages(store, tmp_path / "raw")
    assert result["failed"] == 0
    with store.session() as session:
        row = session.scalar(select(Automobile))
        assert row.body_type == "Wagon"
        assert row.vehicle_category == "car" and row.vehicle_type == "wagon"
        assert row.classification_source == "page_table"


@pytest.mark.parametrize(
    "style,category,subtype",
    [
        ("Sedan", "car", "sedan"),
        ("SUV", "car", "suv"),
        ("Crossover", "car", "crossover"),
        ("Pick-up", "truck", "pickup"),
        ("Truck", "truck", "truck"),
        ("Van", "van", "van"),
        ("Minivan", "car", "minivan"),
        ("MPV", "car", "mpv"),
        ("Bus", "bus", "bus"),
        ("Motorcycle", "motorcycle", "motorcycle"),
        ("Unrecognized", "unknown", "unknown"),
    ],
)
def test_vehicle_categories_keep_detailed_types(style, category, subtype):
    from automobile_scraper.vehicle_information import classify_vehicle

    result = classify_vehicle({"body_style": style, "body_style_source": "page_table"})
    assert result["vehicle_category"] == category
    assert result["vehicle_type"] == subtype
    assert result["classification_source"] == (
        "page_table" if category != "unknown" else "insufficient_evidence"
    )


def test_classification_from_segment_and_unknown_model_names():
    from automobile_scraper.vehicle_information import classify_vehicle

    assert classify_vehicle({"segment": "Pickup"}) == {
        "vehicle_category": "truck",
        "vehicle_type": "pickup",
        "classification_source": "segment",
    }
    assert classify_vehicle({"model_name": "Ford F-150"})["vehicle_category"] == "unknown"


def test_storage_reclassifies_when_body_changes_and_reports_coverage(store):
    from automobile_scraper.reporting import coverage_report

    with store.session() as session, session.begin():
        store.save_brand(session, BrandData(name="Example", url="https://example.com/"))
        store.save_automobile(
            session,
            AutomobileData(
                url=URL,
                name="Example Family",
                brand_name="Example",
                information={"body_style": "SUV", "body_style_source": "page_table"},
            ),
        )
    assert coverage_report(store)["vehicle_types"] == {"suv": 1}
    with store.session() as session, session.begin():
        store.save_automobile(
            session,
            AutomobileData(
                url=URL,
                name="Example Family",
                brand_name="Example",
                information={"body_style": "Truck", "body_style_source": "page_table"},
            ),
        )
    report = coverage_report(store)
    assert report["vehicle_categories"] == {"truck": 1}
    assert report["unclassified_models"] == 0


@pytest.mark.parametrize(
    "description,model,expected",
    [
        (
            "Acura introduced the second generation of the TLX, "
            "giving the sport sedan a new platform.",
            "TLX",
            "sedan",
        ),
        ("The F-150 pickup received an update.", "F-150", "pickup"),
        ("The unknown model was a sedan.", "TLX", None),
        ("Unlike a rival sedan, the TLX had unique styling.", "TLX", None),
        ("The TLX replaced a previous sedan.", "TLX", None),
        ("The TLX had a coupe-like silhouette.", "TLX", None),
        ("The TLX adopted a coupe roofline.", "TLX", None),
        ("The TLX used a sedan-based platform.", "TLX", None),
        ("The TLX was available as a sedan or wagon.", "TLX", None),
        ("The TLX was a sedan and wagon.", "TLX", None),
        ("The TLX was revised. It had new wheels. The TLX was a sedan.", "TLX", None),
    ],
)
def test_intro_body_requires_target_model_and_unambiguous_source_wording(
    description, model, expected
):
    from automobile_scraper.vehicle_information import introductory_body_style

    assert introductory_body_style(description, model) == expected


def test_model_introduction_fills_absent_body_metadata():
    source = """<h1 class="newstitle">Example TLX</h1>
    <div class="fl newstext"><div class="mgbot_20">
    Example introduced the TLX sport sedan with a new platform.
    </div></div>"""
    info = extract_vehicle_information(BeautifulSoup(source, "lxml"), "Example TLX", "Example", URL)
    assert info["body_type"] == "Sedan" and info["vehicle_category"] == "car"
    assert info["body_style_source"] == info["classification_source"] == "model_introduction"
    assert info["metadata_source"] == "page_metadata"
    assert info["doors"] is None
