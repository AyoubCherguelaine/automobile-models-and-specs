"""Pure preprocessing into scalar database columns, with explicit units and missing values."""

import html
import re

from .spec_fields import ENGINE_COLUMNS, FLOAT_COLUMNS, INTEGER_COLUMNS, RAW_SPEC_FIELDS
from .vehicle_information import engine_classifications


class UnknownSpecification(ValueError):
    pass


def label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


FIELD_LOOKUP = {
    (label(section), label(field)): column for (section, field), column in RAW_SPEC_FIELDS.items()
}
NUMBER = r"(?<![\w.,])([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"


def measurement(value: str | None, units: list[tuple[str, float]]) -> float | None:
    """Prefer the listed metric unit; convert only explicitly identified fallback units."""
    if not value:
        return None
    value = html.unescape(value).replace("³", "3").replace("\xa0", " ")
    for unit, factor in units:
        match = re.search(NUMBER + r"\s*(?:" + unit + r")(?![a-z])", value, re.I)
        if match:
            return round(float(match[1].replace(",", "")) * factor, 6)
    return None


def years(name: str, url: str) -> dict:
    """Named model year/range takes precedence over the URL's final four-digit year."""
    year = r"((?:18|19|20)\d{2})"
    name = html.unescape(name)
    match = re.search(r"\b" + year + r"\s*[-–—]\s*(" + year + r"|present)\b", name, re.I)
    end = None
    source = None
    if match:
        start = int(match[1])
        end = int(match[2]) if match[2].isdigit() else None
        if end is not None and end < start:
            end = None
        source = "name"
    else:
        # Do not interpret names like Peugeot 2008 or BMW 2002 as model years.
        prefix = re.match(r"^\s*" + year + r"\b", name)
        suffix = re.search(r"\b" + year + r"(?=\s*(?:Photos|$))", name, re.I)
        if suffix and len(name[: suffix.start()].split()) < 2:
            suffix = None
        match = prefix or suffix
        if match:
            start, source = int(match[1]), "name"
        else:
            match = re.search(r"-" + year + r"(?:-\d{1,3})?\.html(?:[?#].*)?$", url)
            start = int(match[1]) if match else None
            source = "url" if match else None
    return {"year": start, "year_end": end, "year_source": source}


def cylinder_configuration(value: str | None, fuel: str | None) -> dict:
    value = (value or "").strip()
    cylinder_count = rotor_count = None
    engine_type = None
    match = re.match(r"^(VR|V|W|H|B|L|I)\s*(\d{1,2})(?=$|[^\w]|-)", value, re.I)
    inline = re.match(
        r"^(?:inline\s*(\d{1,2})\b(?![- ]*stroke\b)|(\d{1,2})\s*(?:in[- ]?line|inline))\b",
        value,
        re.I,
    )
    rotary = re.match(r"^(?:R\s*(\d)|(?:(\d)[- ]rotor\s+rotary))$", value, re.I)
    if match:
        cylinder_count = int(match[2])
        layout = "I" if match[1].upper() == "L" else match[1].upper()
        engine_type = f"{layout}{cylinder_count}"
    elif inline:
        cylinder_count = int(inline[1] or inline[2])
        engine_type = f"I{cylinder_count}"
    elif rotary:
        rotor_count = int(rotary[1] or rotary[2])
        engine_type = f"R{rotor_count}"
    elif value.isdigit() and 1 <= int(value) <= 16:
        cylinder_count = int(value)
    elif (fuel or "").casefold().strip() == "electric" or "electric motor" in value.casefold():
        engine_type = "Electric"
    else:
        words = {
            "single": 1,
            "one": 1,
            "twin": 2,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "eight": 8,
            "ten": 10,
            "twelve": 12,
        }
        tokens = "|".join(words)
        counts = {
            int(match[1]) if match[1].isdigit() else words[match[1].casefold()]
            for match in re.finditer(rf"\b(1[0-6]|[1-9]|{tokens})[- ]+cylinders?\b", value, re.I)
        }
        twin_layouts = {
            kind
            for pattern, kind in ((r"\bv[- ]twin\b", "V2"), (r"\bparallel[- ]twin\b", "I2"))
            if re.search(pattern, value, re.I)
        }
        if twin_layouts:
            counts.add(2)
        if len(counts) == 1:
            cylinder_count = counts.pop()
            if len(twin_layouts) == 1:
                engine_type = twin_layouts.pop()
    return {"cylinders": cylinder_count, "engine_type": engine_type, "rotors": rotor_count}


def preprocess_engine(specs: dict, name: str, columns: dict | None = None) -> dict:
    """Flatten every supported field and derive numeric values without losing source text."""
    fields = dict.fromkeys(ENGINE_COLUMNS)
    if columns:
        unknown = columns.keys() - fields.keys()
        if unknown:
            raise UnknownSpecification(f"Unknown engine columns: {sorted(unknown)}")
        fields.update(columns)
    for section, values in specs.items():
        for key, value in values.items():
            column = FIELD_LOOKUP.get((label(section), label(key)))
            if column is None:
                raise UnknownSpecification(f"Unmapped source specification: {section} / {key}")
            if not isinstance(value, str):
                raise ValueError(f"Specification {section} / {key} must be text")
            fields[column] = value
    fields.update(cylinder_configuration(fields["cylinders_raw"], fields["fuel"]))
    kw = [(r"kw", 1), (r"bhp", 0.745699872), (r"hp|ps", 0.73549875)]
    nm = [(r"n\s*[-·]?\s*m", 1), (r"lb[- ]?ft|ft[- ]?lb", 1.355817948)]
    kmh = [(r"km\s*/\s*h|kph|kmh", 1), (r"mph", 1.609344)]
    mm = [(r"mm", 1), (r"cm", 10), (r"m", 1000), (r"in(?:ches)?", 25.4)]
    kg = [(r"kg", 1), (r"lbs?", 0.45359237)]
    liters = [(r"l(?:iters?|itres?)?", 1)]
    derived = {
        "power_kw": measurement(fields["power"], kw),
        "power_hp": measurement(fields["power"], [(r"hp|ps", 1)]),
        "torque_nm": measurement(fields["torque"], nm),
        "displacement_cc": measurement(fields["size"], [(r"cm3|cc", 1), (r"l", 1000)]),
        "fuel_capacity_l": measurement(fields["fuel_capacity"], liters),
        "top_speed_kph": measurement(fields["top_speed"], kmh),
        "acceleration_0_100_s": measurement(
            fields["acceleration_0_100"], [(r"s(?:ec(?:onds?)?)?", 1)]
        ),
        "electric_motor_power_kw": measurement(fields["electrical_motor_power"], kw),
        "electric_motor_torque_nm": measurement(fields["electrical_motor_torque"], nm),
        "total_maximum_power_kw": measurement(fields["total_maximum_power"], kw),
        "total_maximum_torque_nm": measurement(fields["total_maximum_torque"], nm),
        "cargo_volume_l": measurement(fields["cargo_volume"], liters),
        "unladen_weight_kg": measurement(fields["unladen_weight"], kg),
        "gross_weight_limit_kg": measurement(fields["gross_weight_limit"], kg),
        "battery_nominal_capacity_kwh": measurement(
            fields["battery_nominal_capacity"], [(r"kwh", 1)]
        ),
        "battery_maximum_capacity_kwh": measurement(
            fields["battery_maximum_capacity"], [(r"kwh", 1)]
        ),
        "battery_range_km": measurement(
            fields["battery_range"], [(r"km", 1), (r"miles?", 1.609344)]
        ),
        "frontal_area_m2": measurement(fields["frontal_area"], [(r"m2", 1)]),
    }
    for column in ("length", "width", "height", "wheelbase", "ground_clearance"):
        derived[column + "_mm"] = measurement(fields[column], mm)
    drag = fields["drag_coefficient"]
    derived["drag_coefficient_value"] = (
        float(drag) if drag and re.fullmatch(r"\d+(?:\.\d+)?", drag.strip()) else None
    )
    for prefix in ("nedc", "epa", "wltp"):
        for raw in fields.copy():
            if not raw.startswith(prefix + "_"):
                continue
            target = raw + ("_g_km" if "co2" in raw else "_l_100km")
            if target in FLOAT_COLUMNS:
                derived[target] = measurement(
                    fields[raw],
                    [(r"g\s*/\s*km", 1)] if "co2" in raw else [(r"l\s*/\s*100\s*km", 1)],
                )
    for column in ("power", "torque"):
        result = measurement(fields[column], [(r"rpm", 1)])
        derived[column + "_rpm"] = int(result) if result is not None else None
    match = re.search(r"\b(\d+)[- ]speed\b", fields["T_gearbox"] or "", re.I)
    derived["gearbox_speeds"] = int(match[1]) if match else None
    fields.update(derived)
    fields.update(engine_classifications(fields, name))
    for column in INTEGER_COLUMNS:
        if fields[column] is not None and not isinstance(fields[column], int):
            raise ValueError(f"{column} must be an integer")
    for column in FLOAT_COLUMNS:
        if fields[column] is not None and not isinstance(fields[column], (float, int)):
            raise ValueError(f"{column} must be numeric")
    return fields
