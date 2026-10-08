from itertools import islice
from pathlib import Path
from xml.etree.ElementTree import SubElement

import pytest
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from ai_stylist.catalog import importer
from ai_stylist.catalog.importer import inspect_feed, xml_elements
from ai_stylist.catalog.mapping import FeedMapping
from ai_stylist.schemas.catalog import CatalogCategory

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_visual_colors_and_offer_identifiers_are_separate(records):
    tops = [r for r in records if r.product.product_type == "blouse"]
    assert len(tops) == 2
    black = next(r for r in tops if r.product.color == "Чёрный")
    assert len(black.offers) == 2
    assert black.offers[0].external_id == "0001"
    assert black.offers[0].sku == "sku-0001"
    assert black.offers[0].barcode == "0000000000001"
    assert black.offers[0].currency == "RUB"
    assert black.offers[0].source_attributes["currencyId"][0]["text"] == "RUR"
    assert {c.external_id for c in black.product.source_categories} == {"tops", "new"}


def test_unverified_mapping_refuses_import(mapping):
    with pytest.raises(ValueError, match="not verified"):
        mapping.model_copy(update={"verified": False}).assert_verified()


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda e: e.set("available", "unknown"), "availability"),
        (lambda e: e.remove(e.find("currencyId")), "currency"),
        (lambda e: setattr(e.find("currencyId"), "text", "USD"), "currency"),
        (lambda e: e.remove(e.find("color")), "unknown color"),
    ],
)
def test_unresolved_mappings_are_not_guessed(mapping, change, match):
    element = next(xml_elements(FIXTURES / "synthetic.xml", "offer"))
    categories = {
        c.external_id: c
        for c in (mapping.category(e) for e in xml_elements(FIXTURES / "synthetic.xml", "category"))
    }
    change(element)
    with pytest.raises(ValueError, match=match):
        mapping.product(element, categories)


def test_inspection_is_bounded_and_preserves_tags():
    result = inspect_feed(FIXTURES / "synthetic.xml", "offer", "category", limit=2)
    assert len(result["offers"]) == len(result["categories"]) == 2
    assert 'id="0001"' in result["offers"][0]


def test_xml_entities_are_rejected():
    with pytest.raises(DefusedXmlException):
        fromstring('<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///not-read">]><x>&secret;</x>')


def test_verified_12storeez_sample_keeps_group_article_and_offer_ids_separate():
    mapping = FeedMapping.model_validate_json(
        (Path(__file__).parents[2] / "catalog_mappings" / "12storeez.json").read_text(
            encoding="utf-8"
        )
    )
    feed = FIXTURES / "12storeez-sample.xml"
    categories = {
        category.external_id: category
        for category in (mapping.category(e) for e in xml_elements(feed, "category"))
    }
    first, second = [mapping.product(e, categories) for e in islice(xml_elements(feed, "offer"), 2)]
    assert first.product.id == second.product.id
    assert first.offers[0].id != second.offers[0].id
    assert first.offers[0].sku == second.offers[0].sku == "22086532"
    assert first.offers[0].barcode != second.offers[0].barcode
    assert {first.offers[0].size, second.offers[0].size} == {"XS", "S"}
    assert first.product.product_type == "trousers"
    assert first.offers[0].currency == "RUB"
    assert not first.offers[0].available


def test_12storeez_category_priority_and_scoped_title_rules():
    mapping = FeedMapping.model_validate_json(
        (Path(__file__).parents[2] / "catalog_mappings" / "12storeez.json").read_text(
            encoding="utf-8"
        )
    )
    feed = FIXTURES / "12storeez-sample.xml"
    categories = {
        category.external_id: category
        for category in (mapping.category(e) for e in xml_elements(feed, "category"))
    }
    categories["100113"] = CatalogCategory(
        id="db79e56f-1db9-4c86-9761-8795290930bf", external_id="100113", name="Джинсы"
    )
    categories["100084"] = CatalogCategory(
        id="51a7b9cb-dbd5-4d5a-84af-c41c74e18c5f",
        external_id="100084",
        name="[Название отсутствует в XML: 100084]",
    )
    jeans = next(xml_elements(feed, "offer"))
    SubElement(jeans, "categoryId").text = "100113"
    assert mapping.product(jeans, categories).product.product_type == "jeans"
    linen_dress = fromstring(
        '<offer id="fixture-dress" group_id="fixture-dress" available="true">'
        "<categoryId>100084</categoryId><currencyId>RUR</currencyId>"
        "<model>Платье льняное</model><article>fixture</article><price>9000</price>"
        '<param code="color">Бежевый</param><param code="size">M</param></offer>'
    )
    assert mapping.product(linen_dress, categories).product.product_type == "dress"
    linen_dress.find("model").text = "Набор мячей"
    assert mapping.product(linen_dress, categories).product.product_type is None


def test_streaming_discards_unrelated_subtrees_but_keeps_target_children(monkeypatch):
    root = fromstring(
        "<catalog><offers><offer><name>Ignored</name></offer></offers>"
        '<categories><category id="x"><name>Preserved</name></category></categories></catalog>'
    )

    def events(element):
        yield "start", element
        for child in element:
            yield from events(child)
        yield "end", element

    stream = list(events(root))
    monkeypatch.setattr(importer, "iterparse", lambda *args, **kwargs: iter(stream))
    reader = xml_elements(Path("not-read.xml"), "category")
    category = next(reader)
    assert root.find("offers") is None
    assert category.get("id") == "x" and category.findtext("name") == "Preserved"
    assert list(reader) == []
    assert len(root) == 0
