import pytest

from ai_stylist.llm.response import validate_rewrite
from ai_stylist.pipeline.service import catalog_cards
from ai_stylist.schemas.intent import StylingBudget, StylingIntent
from ai_stylist.schemas.planning import OutfitComposition, OutfitPlan, OutfitSlot
from ai_stylist.schemas.retrieval import CandidatePool, RetrievalCandidate
from ai_stylist.schemas.styling import StylingResponse, VLMStylingResponse
from ai_stylist.stylist.service import build_styling_request
from ai_stylist.stylist.validation import validate_styling


def pool_for(records):
    roles = {
        "blouse": "top",
        "trousers": "bottom",
        "shoes": "shoes",
        "dress": "dress",
        "suit_set": "set",
        "cardigan": "outer_layer",
        "bag": "bag",
    }
    return CandidatePool(
        products=[
            RetrievalCandidate(
                product=record,
                eligible_slots=[roles[record.product.product_type]],
                retrieval_score=1.0,
            )
            for record in records
        ]
    )


@pytest.fixture
def styling_request(records, plan):
    return build_styling_request(
        StylingIntent(),
        plan,
        pool_for(records),
        {
            "stylist_rules": "rules",
            "compatibility_rubric": "rubric",
        },
    )


def test_valid_selection_and_decimal_totals(styling_request, styling_result, records):
    validate_styling(styling_request, styling_result, records)
    assert str(catalog_cards(styling_result.looks, records)[0].total) == "600.60"
    assert '"price":"100.10"' in styling_request.model_dump_json()


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda r: setattr(r.looks[0], "plan_id", "invented"), "plan"),
        (lambda r: setattr(r.looks[0].items[0], "product_id", "invented"), "Product ID"),
        (lambda r: setattr(r.looks[0].items[0], "offer_id", r.looks[0].items[1].offer_id), "Offer"),
        (lambda r: r.looks[0].items.pop(), "Missing required"),
        (lambda r: r.looks[0].items.append(r.looks[0].items[0]), "repeated"),
    ],
)
def test_invalid_selections_never_pass(styling_request, styling_result, records, mutation, match):
    mutation(styling_result)
    with pytest.raises(ValueError, match=match):
        validate_styling(styling_request, styling_result, records)


def test_outfit_budget_includes_every_item(styling_request, styling_result, records):
    styling_request.intent.budget = StylingBudget(max_outfit_price="600.59")
    with pytest.raises(ValueError, match="outfit exceeds"):
        validate_styling(styling_request, styling_result, records)


def test_stock_and_price_rechecked_after_model_call(styling_request, styling_result, records):
    chosen = styling_result.looks[0].items[0].offer_id
    offer = next(o for r in records for o in r.offers if o.id == chosen)
    offer.available = False
    with pytest.raises(ValueError, match="availability"):
        validate_styling(styling_request, styling_result, records)
    offer.available = True
    offer.price += 1
    with pytest.raises(ValueError, match="changed during"):
        validate_styling(styling_request, styling_result, records)


def test_different_size_skus_are_not_distinct_looks(styling_request, styling_result, records):
    second = styling_result.looks[0].model_copy(deep=True)
    top = next(r for r in records if r.product.id == second.items[0].product_id)
    second.items[0].offer_id = top.offers[1].id
    result = VLMStylingResponse(looks=[styling_result.looks[0], second])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_styling(styling_request, result, records)


def test_set_is_one_offer_and_one_price(records, look_factory):
    plan = OutfitPlan(
        compositions=[
            OutfitComposition(
                plan_id="set",
                slots=[
                    OutfitSlot(role="set", retrieval_query="suit"),
                    OutfitSlot(role="shoes", retrieval_query="shoes"),
                ],
            )
        ]
    )
    request = build_styling_request(
        StylingIntent(budget=StylingBudget(max_outfit_price="800.30")),
        plan,
        pool_for(records),
        {"stylist_rules": "rules", "compatibility_rubric": "rubric"},
    )
    result = VLMStylingResponse(
        looks=[look_factory(records, ("set", "shoes"), "set")],
        limitations=["One available outfit."],
    )
    validate_styling(request, result, records)
    assert str(catalog_cards(result.looks, records)[0].total) == "800.30"
    assert len(result.looks[0].items) == 2


def test_response_rewrite_preserves_items_scores_and_limitations(styling_result):
    response = StylingResponse(
        message="Готово", looks=styling_result.looks, limitations=styling_result.limitations
    )
    validate_rewrite(styling_result, response)
    response.limitations = []
    with pytest.raises(ValueError, match="limitations"):
        validate_rewrite(styling_result, response)
    response.limitations = styling_result.limitations
    response.looks = [look.model_copy(deep=True) for look in styling_result.looks]
    response.looks[0].items[0].offer_id = "changed"
    with pytest.raises(ValueError, match="assignments"):
        validate_rewrite(styling_result, response)
