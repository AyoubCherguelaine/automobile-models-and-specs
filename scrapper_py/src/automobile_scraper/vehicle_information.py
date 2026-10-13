"""Extract vehicle identity/types from explicit page metadata, with conservative classifiers."""

import html
import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

VEHICLE_TEXT_COLUMNS = (
    "model_name",
    "model_family",
    "body_style",
    "body_type",
    "vehicle_category",
    "vehicle_type",
    "classification_source",
    "segment",
    "generation",
    "trim",
    "vehicle_configuration",
    "production_years_text",
    "assembly_location",
    "infotainment",
    "autonomous_driving",
    "main_image_url",
    "body_style_source",
    "metadata_source",
)
VEHICLE_INTEGER_COLUMNS = ("doors", "seats", "autonomy_level")
VEHICLE_BOOLEAN_COLUMNS = ("supports_apple_carplay", "supports_android_auto")
VEHICLE_COLUMNS = (*VEHICLE_TEXT_COLUMNS, *VEHICLE_INTEGER_COLUMNS, *VEHICLE_BOOLEAN_COLUMNS)
ENGINE_TYPE_COLUMNS = (
    "powertrain_type",
    "fuel_type",
    "aspiration",
    "injection_type",
    "transmission_type",
    "drive_layout",
)


def clean_model_name(name: str, brand: str) -> str:
    value = html.unescape(name)
    value = re.split(r"\s+Photos\s*,?", value, flags=re.I)[0]
    value = re.sub(r"^\s*(?:18|19|20)\d{2}\s+", "", value)
    value = re.sub(
        r"\s*\(?\b(?:18|19|20)\d{2}\s*[-–—]\s*(?:(?:18|19|20)\d{2}|Present)\)?\s*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s+", " ", value).strip()
    pattern = re.escape(brand.strip()).replace(r"\ ", r"\s+")
    value = re.sub(r"^" + pattern + r"\s+", "", value, flags=re.I)
    # A trailing date is removed only when another model-name token precedes it.
    if len(value.split()) > 1:
        value = re.sub(r"\s+\(?(?:18|19|20)\d{2}\)?$", "", value)
    return value.strip()


def body_type(style: str | None) -> str | None:
    value = (style or "").casefold()
    for pattern, kind in (
        (r"\bbus\b|\bcoach\b", "Bus"),
        (r"\bmotorcycle\b|\bmotorbike\b", "Motorcycle"),
        (r"\bsuv\b|sports?[- ]utility", "SUV"),
        (r"\bcrossover\b", "Crossover"),
        (r"\bpick[- ]?up\b", "Pickup"),
        (r"\bminivan\b", "Minivan"),
        (r"\broadster\b", "Roadster"),
        (r"convertible|cabrio|spider|spyder", "Convertible"),
        (r"wagon|estate|combi|touring", "Wagon"),
        (r"hatchback", "Hatchback"),
        (r"sedan|saloon", "Sedan"),
        (r"coup[eé]", "Coupe"),
        (r"\bmpv\b", "MPV"),
        (r"\bvan\b", "Van"),
        (r"\btruck\b", "Truck"),
        (r"limousine", "Limousine"),
    ):
        if re.search(pattern, value):
            return kind
    return None


# Product taxonomy, not legal registration/weight classes. Preserve detailed body style.
TYPE_GROUPS = {
    "SUV": ("car", "suv"),
    "Crossover": ("car", "crossover"),
    "Pickup": ("truck", "pickup"),
    "Truck": ("truck", "truck"),
    "Van": ("van", "van"),
    "Minivan": ("car", "minivan"),
    "MPV": ("car", "mpv"),
    "Bus": ("bus", "bus"),
    "Motorcycle": ("motorcycle", "motorcycle"),
    "Roadster": ("car", "roadster"),
    "Convertible": ("car", "convertible"),
    "Wagon": ("car", "wagon"),
    "Hatchback": ("car", "hatchback"),
    "Sedan": ("car", "sedan"),
    "Coupe": ("car", "coupe"),
    "Limousine": ("car", "limousine"),
}


def classify_vehicle(fields: dict) -> dict:
    """Classify only explicit body/segment evidence, keeping unknown records visible."""
    kind = body_type(fields.get("body_style")) or fields.get("body_type")
    source = fields.get("body_style_source") or "body_type"
    if kind not in TYPE_GROUPS:
        kind = body_type(fields.get("segment"))
        source = "segment"
    category, subtype = TYPE_GROUPS.get(kind, ("unknown", "unknown"))
    return {
        "vehicle_category": category,
        "vehicle_type": subtype,
        "classification_source": source if category != "unknown" else "insufficient_evidence",
    }


def introductory_body_style(description: str | None, model_name: str) -> str | None:
    """Use explicit body wording tied to the target model in its opening sentences."""
    if not description or not model_name:
        return None
    plain = BeautifulSoup(html.unescape(description), "lxml").get_text(" ", strip=True)
    sentences = re.split(r"(?<=[.!?])\s+", plain)[:2]
    model = re.escape(model_name).replace(r"\ ", r"\s+")
    styles = []
    for sentence in sentences:
        if not re.search(r"(?<!\w)" + model + r"(?!\w)", sentence, re.I):
            continue
        # Comparisons and historical/alternative variants do not describe the target body.
        if re.search(
            r"\b(?:unlike|rival|compared|instead|previous|predecessor|replaced|"
            r"based on|alongside|rather than|available as|offered as)\b",
            sentence,
            re.I,
        ):
            continue
        for match in re.finditer(
            r"\b(?:sport[- ]utility vehicle|sports utility vehicle|SUV|crossover|pickup|pick-up|"
            r"minivan|MPV|sedan|saloon|hatchback|station wagon|wagon|coupe|coupé|"
            r"convertible|cabriolet|roadster|van|truck|limousine)\b",
            sentence,
            re.I,
        ):
            suffix = sentence[match.end() :]
            if re.match(
                r"(?:[- ](?:like|inspired|style|based)\b|\s+(?:styling|roofline|"
                r"appearance|profile|design|looks|platform)\b)",
                suffix,
                re.I,
            ):
                continue
            if body_type(match[0]):
                styles.append(match[0])
    kinds = {body_type(value) for value in styles}
    return styles[0] if len(kinds) == 1 else None


def scalar(value):
    if isinstance(value, dict):
        value = value.get("name") or value.get("value")
    return html.unescape(str(value)).strip() if isinstance(value, (str, int, float)) else None


def positive_count(value) -> int | None:
    value = scalar(value)
    if value and re.fullmatch(r"\d+", value) and 0 < int(value) < 100:
        return int(value)
    return None


def car_schema(page: BeautifulSoup) -> dict:
    def objects(value):
        if isinstance(value, list):
            for item in value:
                yield from objects(item)
        elif isinstance(value, dict):
            yield value
            if "@graph" in value:
                yield from objects(value["@graph"])

    for script in page.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        for item in objects(data):
            kind = item.get("@type", [])
            if isinstance(kind, str):
                kind = [kind]
            if any(str(value).rsplit("/", 1)[-1] in {"Car", "Vehicle"} for value in kind):
                return item
    return {}


def extract_vehicle_information(page: BeautifulSoup, name: str, brand: str, url: str) -> dict:
    fields = dict.fromkeys(VEHICLE_COLUMNS)
    fields["model_name"] = clean_model_name(name, brand)
    schema = car_schema(page)
    # Summary tables can be siblings of the title container. Read only known labels.
    labels = {
        "body style",
        "segment",
        "production years",
        "assembled at",
        "infotainment",
        "autonomous driving",
        "generation",
        "trim",
        "doors",
        "number of doors",
        "seats",
        "seating capacity",
    }
    details = {}
    for row in page.select("tr"):
        cells = row.select("td")
        if len(cells) == 2 and cells[0].select_one("b"):
            key = cells[0].get_text(" ", strip=True).strip(": ").casefold()
            if key in labels:
                details[key] = cells[1].get_text(" ", strip=True)
    assignments = {
        "body style": "body_style",
        "segment": "segment",
        "production years": "production_years_text",
        "assembled at": "assembly_location",
        "infotainment": "infotainment",
        "autonomous driving": "autonomous_driving",
        "generation": "generation",
        "trim": "trim",
    }
    for key, column in assignments.items():
        if details.get(key):
            fields[column] = details[key]
    if fields["body_style"]:
        fields["body_style_source"] = "page_table"
    elif scalar(schema.get("bodyType")):
        fields["body_style"] = scalar(schema["bodyType"])
        fields["body_style_source"] = "structured_data"
    if not fields["body_style"]:
        description = page.select(".fl.newstext .mgbot_20")
        introductory_text = (
            description[-1].get_text(" ", strip=True)
            if description
            else scalar(schema.get("description"))
        )
        style = introductory_body_style(introductory_text, fields["model_name"])
        if style:
            fields["body_style"] = style
            fields["body_style_source"] = "model_introduction"
    fields["body_type"] = body_type(fields["body_style"])
    fields["segment"] = fields["segment"] or scalar(schema.get("category"))
    fields["generation"] = fields["generation"] or scalar(schema.get("vehicleGeneration"))
    fields["vehicle_configuration"] = scalar(schema.get("vehicleConfiguration"))
    crumbs = page.select('[itemprop="itemListElement"]')
    if len(crumbs) >= 5 and crumbs[3].select_one("a[href]"):
        fields["model_family"] = crumbs[3].get_text(" ", strip=True)
    fields["model_family"] = fields["model_family"] or scalar(schema.get("model"))
    fields["doors"] = positive_count(
        details.get("doors") or details.get("number of doors") or schema.get("numberOfDoors")
    )
    fields["seats"] = positive_count(
        details.get("seats")
        or details.get("seating capacity")
        or schema.get("vehicleSeatingCapacity")
    )
    # Never infer doors from 'two-door coupe': the source can contradict the actual vehicle.
    image = schema.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("contentUrl") or image.get("url")
    if scalar(image):
        fields["main_image_url"] = urljoin(url, scalar(image))
    infotainment = (fields["infotainment"] or "").casefold()
    if "apple carplay" in infotainment:
        fields["supports_apple_carplay"] = True
    if "android auto" in infotainment:
        fields["supports_android_auto"] = True
    match = re.search(r"\blevel\s*([0-5])\b", fields["autonomous_driving"] or "", re.I)
    fields["autonomy_level"] = int(match[1]) if match else None
    fields["metadata_source"] = (
        "page_metadata" if details or schema or fields["body_style"] else "title_only"
    )
    fields.update(classify_vehicle(fields))
    return fields


def engine_classifications(fields: dict, name: str) -> dict:
    fuel = (fields.get("fuel") or "").casefold()
    system = (fields.get("fuel_system") or "").casefold()
    gearbox = (fields.get("T_gearbox") or "").casefold()
    drive = (fields.get("T_drive_type") or "").casefold()
    result = dict.fromkeys(ENGINE_TYPE_COLUMNS)
    result["electric_motor_count"] = None
    if "fuel cell" in fuel:
        result["powertrain_type"] = "Hydrogen fuel cell"
    elif "plug" in fuel and "hybrid" in fuel:
        result["powertrain_type"] = "Plug-in hybrid"
    elif "mild" in fuel and "hybrid" in fuel:
        result["powertrain_type"] = "Mild hybrid"
    elif "hybrid" in fuel:
        result["powertrain_type"] = "Hybrid"
    elif fuel.strip() == "electric":
        result["powertrain_type"] = "Electric"
    elif fuel:
        result["powertrain_type"] = "Combustion"
    for marker, value in (
        ("gasoline", "Gasoline"),
        ("petrol", "Gasoline"),
        ("diesel", "Diesel"),
        ("ethanol", "Ethanol"),
        ("natural gas", "CNG"),
        ("lpg", "LPG"),
        ("hydrogen", "Hydrogen"),
        ("electric", "Electric"),
    ):
        if marker in fuel:
            result["fuel_type"] = value
            break
    turbo, supercharged = "turbo" in system, "supercharg" in system
    if turbo and supercharged:
        result["aspiration"] = "Twincharged"
    elif turbo:
        result["aspiration"] = "Turbocharged"
    elif supercharged:
        result["aspiration"] = "Supercharged"
    elif "naturally aspirated" in system:
        result["aspiration"] = "Naturally aspirated"
    if "common rail" in system:
        result["injection_type"] = "Common rail"
    elif "direct" in system and "multipoint" in system:
        result["injection_type"] = "Direct and multipoint injection"
    else:
        for marker, value in (
            ("multipoint", "Multipoint injection"),
            ("monopoint", "Monopoint injection"),
            ("indirect", "Indirect injection"),
            ("direct", "Direct injection"),
            ("carburetor", "Carburetor"),
            ("carburettor", "Carburetor"),
            ("electronic", "Electronic injection"),
        ):
            if marker in system:
                result["injection_type"] = value
                break
    for pattern, value in (
        (r"\bcvt\b|continuously variable", "CVT"),
        (r"dual[- ]clutch|\bdct\b|\bdsg\b|\bpdk\b", "Dual-clutch"),
        (r"automated manual|robotized|robotised", "Automated manual"),
        (r"single[- ]speed|1[- ]speed", "Single-speed"),
        (r"automatic", "Automatic"),
        (r"manual", "Manual"),
    ):
        if re.search(pattern, gearbox):
            result["transmission_type"] = value
            break
    for pattern, value in (
        (r"all[- ]wheel|\bawd\b", "AWD"),
        (r"four[- ]wheel|4[- ]wheel|\b4wd\b|4x4", "4WD"),
        (r"front[- ]wheel|\bfwd\b", "FWD"),
        (r"rear[- ]wheel|\brwd\b", "RWD"),
    ):
        if re.search(pattern, drive):
            result["drive_layout"] = value
            break
    motor_text = name + " " + (fields.get("cylinders_raw") or "")
    match = re.search(
        r"\b(single|dual|twin|tri|triple|quad|one|two|three|four|[1-4])[- ]+motors?\b",
        motor_text,
        re.I,
    )
    if match:
        counts = {
            "single": 1,
            "one": 1,
            "dual": 2,
            "twin": 2,
            "two": 2,
            "tri": 3,
            "triple": 3,
            "three": 3,
            "quad": 4,
            "four": 4,
        }
        result["electric_motor_count"] = counts.get(match[1].casefold()) or int(match[1])
    return result


def title_information(name: str, brand: str) -> dict:
    fields = {"model_name": clean_model_name(name, brand), "metadata_source": "title_only"}
    # These are explicit body designations, not model-to-body guesses.
    match = re.search(
        r"\b(station wagon|hatchback|convertible|cabriolet|cabrio|roadster|"
        r"pick[- ]?up|sedan|saloon|wagon|coupe|coupé|minivan|suv|mpv|van|truck|bus|motorcycle)\b",
        fields["model_name"],
        re.I,
    )
    if match:
        fields.update(body_style=match[1], body_type=body_type(match[1]), body_style_source="title")
    generation = re.search(r"\bMk\s*(?:[IVX]+|\d+)\b", fields["model_name"], re.I)
    if generation:
        fields["generation"] = generation[0]
    return fields
