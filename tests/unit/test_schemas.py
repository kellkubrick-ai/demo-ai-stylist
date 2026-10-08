import pytest
from pydantic import ValidationError

from ai_stylist.schemas.enrichment import ProductEnrichment
from ai_stylist.schemas.intent import StylingBudget, StylingIntent
from ai_stylist.schemas.planning import (
    OutfitComposition,
    OutfitPlan,
    OutfitSlot,
    StylingUnderstanding,
)
from ai_stylist.schemas.styling import LookScore, VLMStylingResponse


def test_budget_uses_decimal_and_checks_bounds():
    budget = StylingBudget(max_outfit_price="0.30")
    assert str(budget.max_outfit_price) == "0.30"
    with pytest.raises(ValidationError):
        StylingBudget(min_item_price="100", max_item_price="50")
    with pytest.raises(ValidationError):
        StylingBudget(max_item_price="NaN")


def test_clarification_and_plan_are_consistent():
    with pytest.raises(ValidationError):
        StylingIntent(needs_clarification=True)
    with pytest.raises(ValidationError):
        StylingUnderstanding(intent=StylingIntent(), outfit_plan=OutfitPlan())
    result = StylingUnderstanding(
        intent=StylingIntent(needs_clarification=True, clarification_question="Какой повод?"),
        outfit_plan=OutfitPlan(),
    )
    assert result.intent.needs_clarification


@pytest.mark.parametrize(
    "roles",
    [
        ["top", "bottom"],
        ["dress", "top", "bottom", "shoes"],
        ["dress", "dress", "shoes"],
    ],
)
def test_invalid_compositions_rejected(roles):
    with pytest.raises(ValidationError):
        OutfitComposition(plan_id="x", slots=[OutfitSlot(role=r, retrieval_query=r) for r in roles])


def test_unknown_type_for_role_is_rejected():
    with pytest.raises(ValidationError):
        OutfitSlot(role="shoes", product_type_constraints=["dress"], retrieval_query="shoes")


def test_duplicate_plan_ids_are_rejected(plan):
    with pytest.raises(ValidationError):
        OutfitPlan(compositions=plan.compositions * 2)


def test_reduced_results_need_limitations():
    with pytest.raises(ValidationError):
        VLMStylingResponse()
    assert VLMStylingResponse(limitations=["Пул не содержит обуви."]).looks == []


def test_scores_cannot_be_nan_or_out_of_range():
    values = {key: 0.8 for key in LookScore.model_fields}
    for value in (float("nan"), 1.1, -0.1):
        with pytest.raises(ValidationError):
            LookScore(**{**values, "overall_score": value})


def test_enrichment_cannot_invent_commercial_fields():
    with pytest.raises(ValidationError):
        ProductEnrichment(price=100, brand="invented")
