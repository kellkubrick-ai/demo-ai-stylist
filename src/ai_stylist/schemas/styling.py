from decimal import Decimal
from typing import Any, Self

from pydantic import Field, model_validator

from ai_stylist.schemas.base import Schema
from ai_stylist.schemas.enrichment import ProductEnrichment
from ai_stylist.schemas.intent import OutfitRole, StylingIntent
from ai_stylist.schemas.planning import OutfitPlan
from ai_stylist.schemas.retrieval import MissingRequiredSlot


class VLMOfferCandidate(Schema):
    offer_id: str
    eligible_slots: list[OutfitRole] = Field(min_length=1)
    size: str | None = None
    price: Decimal = Field(ge=0)
    currency: str


class VLMProductCandidate(Schema):
    product_id: str
    eligible_slots: list[OutfitRole] = Field(min_length=1)
    title: str
    product_type: str
    source_category_names: list[str] = Field(default_factory=list)
    color: str | None = None
    brand: str | None = None
    original_description: str | None = None
    catalog_attributes: dict[str, Any] = Field(default_factory=dict)
    inferred_styling_attributes: ProductEnrichment | None = None
    eligible_offers: list[VLMOfferCandidate] = Field(min_length=1)
    image_urls: list[str] = Field(min_length=1, max_length=2)


class VLMStylingRequest(Schema):
    intent: StylingIntent
    outfit_plan: OutfitPlan
    products: list[VLMProductCandidate] = Field(min_length=1, max_length=30)
    missing_required_slots: list[MissingRequiredSlot] = Field(default_factory=list)
    stylist_rules: str
    compatibility_rubric: str


class LookScore(Schema):
    style_match: float = Field(ge=0, le=1)
    color_compatibility: float = Field(ge=0, le=1)
    silhouette_compatibility: float = Field(ge=0, le=1)
    occasion_match: float = Field(ge=0, le=1)
    user_preferences_match: float = Field(ge=0, le=1)
    overall_score: float = Field(ge=0, le=1)


class SelectedLookItem(Schema):
    slot: OutfitRole
    product_id: str
    offer_id: str


class GeneratedLook(Schema):
    plan_id: str
    items: list[SelectedLookItem] = Field(min_length=1)
    score: LookScore
    explanation: str


class VLMStylingResponse(Schema):
    looks: list[GeneratedLook] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_limitations(self) -> Self:
        if len(self.looks) < 2 and not any(s.strip() for s in self.limitations):
            raise ValueError("Fewer than two looks require a grounded limitation")
        return self


class ResponseRequest(Schema):
    intent: StylingIntent
    styling_result: VLMStylingResponse


class StylingResponse(Schema):
    message: str
    looks: list[GeneratedLook] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list)
    follow_up_question: str | None = None
