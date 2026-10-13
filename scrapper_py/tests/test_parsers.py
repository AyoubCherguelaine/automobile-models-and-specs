import pytest
from conftest import BRANDS, DETAIL, FINDER, RESULTS

from automobile_scraper.config import Settings
from automobile_scraper.parsers import (
    ParseError,
    parse_automobile,
    parse_brand_ids,
    parse_brands,
    parse_model_urls,
    parse_press_release,
    press_release_id,
)

BASE = "https://www.autoevolution.com"


def test_complete_source_contract():
    assert str(parse_brands(BRANDS, BASE)[0].url) == BASE + "/ac/"
    assert parse_brand_ids(FINDER) == ["12"]
    assert parse_model_urls(RESULTS, BASE) == [BASE + "/cars/ac-ace.html"]
    data = parse_automobile(DETAIL, BASE + "/cars/ac-ace.html")
    assert data.name == "AC Ace & Specs"
    assert data.brand_name == "AC"
    assert data.description == "<p>Description</p>"
    assert data.photos == [BASE + "/photo.jpg"]
    assert data.engines[0].specs == {"Engine Specs": {"Power:": "200 kW\n300 HP"}}
    assert press_release_id(DETAIL) == "123"
    assert (
        parse_press_release('<div class="content"><p style="x">Release</p></div>')
        == "<p>Release</p>"
    )


@pytest.mark.parametrize(
    "parser",
    [
        parse_brand_ids,
        lambda html: parse_brands(html, BASE),
        lambda html: parse_model_urls(html, BASE),
        lambda html: parse_automobile(html, BASE + "/car"),
    ],
)
def test_markup_drift_is_explicit(parser):
    with pytest.raises(ParseError):
        parser("<html>Unexpected page</html>")


def test_configuration_validation():
    with pytest.raises(ValueError):
        Settings(concurrency=0, _env_file=None)
    with pytest.raises(ValueError):
        Settings(base_url="invalid", _env_file=None)
