import os
from pathlib import Path

import pytest

from ai_stylist.catalog.importer import xml_elements
from ai_stylist.catalog.mapping import FeedMapping
from ai_stylist.schemas.catalog import CatalogProduct
from ai_stylist.schemas.planning import OutfitComposition, OutfitPlan, OutfitSlot
from ai_stylist.schemas.styling import (
    GeneratedLook,
    LookScore,
    SelectedLookItem,
    VLMStylingResponse,
)

FIXTURES = Path(__file__).parent / "fixtures"
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


@pytest.fixture
def mapping():
    return FeedMapping.model_validate_json((FIXTURES / "synthetic.mapping.json").read_text())


@pytest.fixture
def records(mapping):
    categories = {
        c.external_id: c
        for c in (
            mapping.category(element)
            for element in xml_elements(FIXTURES / "synthetic.xml", "category")
        )
    }
    grouped = {}
    for element in xml_elements(FIXTURES / "synthetic.xml", "offer"):
        record = mapping.product(element, categories)
        if record.product.id in grouped:
            grouped[record.product.id].offers.extend(record.offers)
        else:
            grouped[record.product.id] = record
    return list(grouped.values())


@pytest.fixture
def plan():
    return OutfitPlan(
        compositions=[
            OutfitComposition(
                plan_id="separates",
                slots=[
                    OutfitSlot(role="top", retrieval_query="minimal blouse"),
                    OutfitSlot(role="bottom", retrieval_query="trousers"),
                    OutfitSlot(role="shoes", retrieval_query="flat shoes"),
                    OutfitSlot(role="bag", required=False, retrieval_query="bag"),
                ],
            )
        ]
    )


def record_by_type(records, product_type):
    return next(r for r in records if r.product.product_type == product_type)


def make_look(records: list[CatalogProduct], roles=("top", "bottom", "shoes"), plan_id="separates"):
    types = {
        "top": "blouse",
        "bottom": "trousers",
        "shoes": "shoes",
        "dress": "dress",
        "set": "suit_set",
    }
    items = []
    for role in roles:
        record = record_by_type(records, types[role])
        offer = next(o for o in record.offers if o.available)
        items.append(SelectedLookItem(slot=role, product_id=record.product.id, offer_id=offer.id))
    return GeneratedLook(
        plan_id=plan_id,
        items=items,
        score=LookScore(**{key: 0.8 for key in LookScore.model_fields}),
        explanation="Согласованные цвета и пропорции.",
    )


@pytest.fixture
def styling_result(records):
    return VLMStylingResponse(
        looks=[make_look(records)], limitations=["Доступен один сильный образ."]
    )


@pytest.fixture
def look_factory():
    return make_look
