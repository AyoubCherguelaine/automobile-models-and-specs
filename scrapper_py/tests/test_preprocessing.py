import pytest

from automobile_scraper.countries import brand_country
from automobile_scraper.preprocessing import (
    UnknownSpecification,
    cylinder_configuration,
    measurement,
    preprocess_engine,
    years,
)


def test_common_engine_columns_and_units():
    fields = preprocess_engine(
        {
            "Engine Specs": {
                "Cylinders:": "V6",
                "Displacement:": "2998 cm3",
                "Power:": "257.4 kW @ 6500 RPM\n350 HP @ 6500 RPM",
                "Torque:": "300 lb-ft @ 4000 RPM\n407 Nm @ 4000 RPM",
                "Fuel:": "Gasoline",
                "Fuel Capacity:": "23.8 gallons (90.1 L)",
            },
            "Performance Specs": {"Top Speed:": "155 mph (249 km/h)"},
            "Transmission Specs": {"Drive Type:": "Rear Wheel Drive", "Gearbox:": "6-Speed Manual"},
            "Dimensions": {"Width:": "62.6 in (1,590 mm)"},
        },
        "V6",
    )
    assert fields["power_kw"] == 257.4
    assert fields["power_hp"] == 350
    assert fields["power_rpm"] == 6500
    assert fields["torque_nm"] == 407
    assert fields["cylinders"] == 6 and fields["engine_type"] == "V6"
    assert fields["displacement_cc"] == 2998
    assert fields["fuel_capacity_l"] == 90.1
    assert fields["top_speed_kph"] == 249
    assert fields["width_mm"] == 1590
    assert fields["gearbox_speeds"] == 6
    assert fields["T_drive_type"] == "Rear Wheel Drive"
    assert fields["T_gearbox"] == "6-Speed Manual"
    assert fields["power"].startswith("257.4 kW")


@pytest.mark.parametrize(
    "value,count,kind,rotors",
    [
        ("L4", 4, "I4", None),
        ("Inline 6", 6, "I6", None),
        ("4 In-Line", 4, "I4", None),
        ("V 12", 12, "V12", None),
        ("VR6", 6, "VR6", None),
        ("H4", 4, "H4", None),
        ("R2", None, "R2", 2),
        ("2-Rotor Rotary", None, "R2", 2),
        ("4", 4, None, None),
        ("L$", None, None, None),
        ("air-cooled, two-stroke, twin cylinder", 2, None, None),
        ("Single-cylinder, four-stroke", 1, None, None),
        ("4-stroke, 4-cylinder, four valves per cylinder", 4, None, None),
        ("four-stroke, 4 valves per cylinder", None, None, None),
        ("Inline 4-stroke", None, None, None),
        ("single-cylinder or twin-cylinder", None, None, None),
        ("liquid-cooled, four-stroke V-twin", 2, "V2", None),
        ("Liquid-cooled, DOHC, parallel twin", 2, "I2", None),
        ("four-cylinder or V-twin", None, None, None),
    ],
)
def test_engine_configurations(value, count, kind, rotors):
    assert cylinder_configuration(value, "Gasoline") == {
        "cylinders": count,
        "engine_type": kind,
        "rotors": rotors,
    }


def test_electric_hybrid_and_missing_data():
    fields = preprocess_engine(
        {
            "Engine Specs": {"Fuel:": "Electric"},
            "Power System Specs": {
                "Nominal Capacity:": "102 kWh",
                "Range:": "288 Miles (463.5 Km)",
            },
        },
        "EV",
    )
    assert fields["engine_type"] == "Electric"
    assert fields["cylinders"] is None and fields["displacement_cc"] is None
    assert fields["battery_nominal_capacity_kwh"] == 102
    assert fields["battery_range_km"] == 463.5
    assert cylinder_configuration("V6", "Hybrid Gasoline")["engine_type"] == "V6"
    assert measurement("N/A", [(r"kw", 1)]) is None
    assert measurement("24 gallons", [(r"l", 1)]) is None
    assert measurement("100 mph", [(r"km/h", 1), (r"mph", 1.609344)]) == 160.9344


@pytest.mark.parametrize(
    "name,url,year,end,source",
    [
        ("AC Ace 1993-1996 Photos, engines & specs", "", 1993, 1996, "name"),
        ("ACURA RDX 2021-Present Photos", "", 2021, None, "name"),
        ("2024 Acura Integra", "https://x/cars/acura-integra-2023.html", 2024, None, "name"),
        ("BMW 2002", "https://x/cars/bmw-2002-1973.html", 1973, None, "url"),
        ("PEUGEOT 2008 Photos", "https://x/cars/peugeot-2008-2024-1.html", 2024, None, "url"),
        ("Unknown model", "https://x/no-date.html", None, None, None),
    ],
)
def test_year_ranges(name, url, year, end, source):
    assert years(name, url) == {"year": year, "year_end": end, "year_source": source}


def test_country_origin_lookup():
    assert brand_country("ac") == "United Kingdom"
    assert brand_country("BMW") == "Germany"
    assert brand_country("BUFORI") == "Australia"
    assert brand_country("Unknown") is None


def test_new_source_fields_do_not_disappear_silently():
    with pytest.raises(UnknownSpecification):
        preprocess_engine({"Engine Specs": {"New field:": "123"}}, "Engine")
