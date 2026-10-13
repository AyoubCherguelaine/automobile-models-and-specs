"""Pure HTML parsers. Network and database access belong outside this module."""

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .domain import AutomobileData, BrandData, EngineData
from .vehicle_information import extract_vehicle_information


class ParseError(ValueError):
    pass


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def text(node: Tag | None) -> str:
    return node.get_text(" ", strip=True) if node else ""


def clean_html(node: Tag | None) -> str | None:
    if node is None:
        return None
    for unsafe in node.select("script, style, iframe, object"):
        unsafe.decompose()
    for child in node.find_all(True):
        child.attrs.clear()
    return node.decode_contents().strip() or None


def parse_brands(html: str, base_url: str) -> list[BrandData]:
    result = []
    for node in soup(html).select(".carman"):
        link = node.select_one('[itemprop="url"]')
        name = node.select_one('[itemprop="name"]')
        logo = node.select_one('[itemprop="logo"]')
        url = (link.get("content") or link.get("href")) if link else None
        if not url or not text(name):
            raise ParseError("Brand card missing URL or name")
        result.append(
            BrandData(
                url=urljoin(base_url, url),
                name=text(name),
                logo=urljoin(base_url, logo.get("src")) if logo and logo.get("src") else None,
            )
        )
    if not result:
        raise ParseError("No brand cards (.carman); source markup may have changed")
    return result


def parse_brand_ids(html: str) -> list[str]:
    rows = soup(html).select(".cfrow")
    if len(rows) < 2:
        raise ParseError("Car finder brand selector missing")
    ids = list(
        dict.fromkeys(
            str(node["data-opt"])
            for node in rows[1].select("ul [data-opt]")
            if str(node["data-opt"]).isdigit()
        )
    )
    if not ids:
        raise ParseError("Car finder has no numeric brand IDs")
    return ids


def parse_model_urls(html: str, base_url: str) -> list[str]:
    urls = list(
        dict.fromkeys(urljoin(base_url, node["href"]) for node in soup(html).select("h5 a[href]"))
    )
    if not urls:
        raise ParseError("No automobile results (h5 a); source markup may have changed")
    return urls


def press_release_id(html: str) -> str | None:
    node = soup(html).select_one('[onclick^="aeshowpress("]')
    match = re.search(r"aeshowpress\(\s*(\d+)\s*,", str(node.get("onclick"))) if node else None
    return match[1] if match else None


def parse_press_release(html: str) -> str:
    result = clean_html(soup(html).select_one(".content"))
    if not result:
        raise ParseError("Press release .content missing")
    return result


def parse_automobile(html: str, url: str) -> AutomobileData:
    page = soup(html)
    name = text(page.select_one(".newstitle"))
    crumbs = page.select('[itemprop="itemListElement"]')
    brand = text(crumbs[2]) if len(crumbs) > 2 else ""
    if not name or not brand:
        raise ParseError("Automobile title or brand breadcrumb missing")
    descriptions = page.select(".fl.newstext .mgbot_20")
    photos = None
    gallery = page.select_one("#schema-gallery-data")
    if gallery and gallery.get_text(strip=True):
        try:
            data = json.loads(gallery.get_text())
            photos = [urljoin(url, media["contentUrl"]) for media in data["associatedMedia"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise ParseError("Invalid gallery JSON") from exc
    engines = []
    seen = set()
    for variant in page.select("[data-engid]"):
        engine_name = text(variant.select_one(".enginedata .title .col-green"))
        if not engine_name:
            continue  # Some pages have tab links carrying the same data attribute.
        engine_id = int(variant["data-engid"])
        if engine_id in seen:
            continue
        seen.add(engine_id)
        specs = {}
        for section in variant.select(".techdata"):
            title = text(section.select_one(".title"))
            if not title:
                raise ParseError("Engine specification section has no title")
            title = "Engine Specs" if "ENGINE SPECS" in title.upper() else title.title()
            values = {}
            for row in section.select("tr"):
                cells = row.select("td")
                if len(cells) == 2:
                    # Preserve value capitalization and units rather than changing kW/CO2/RPM.
                    values[text(cells[0]).title()] = cells[1].get_text("\n", strip=True)
            specs[title] = values
        engines.append(EngineData(other_id=engine_id, name=engine_name, specs=specs))
    return AutomobileData(
        url=url,
        brand_name=brand,
        name=name,
        description=clean_html(descriptions[-1]) if descriptions else None,
        photos=photos,
        engines=engines,
        information=extract_vehicle_information(page, name, brand, url),
    )
