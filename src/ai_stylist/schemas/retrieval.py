from pydantic import Field

from ai_stylist.schemas.base import Schema
from ai_stylist.schemas.catalog import CatalogProduct
from ai_stylist.schemas.intent import OutfitRole, StylingIntent
from ai_stylist.schemas.planning import OutfitPlan


class RetrievalRequest(Schema):
    intent: StylingIntent
    outfit_plan: OutfitPlan
    max_products: int = Field(default=30, ge=1, le=30)


class RetrievalCandidate(Schema):
    product: CatalogProduct
    eligible_slots: list[OutfitRole] = Field(min_length=1)
    retrieval_score: float


class MissingRequiredSlot(Schema):
    plan_id: str
    slot: OutfitRole


class CandidatePool(Schema):
    products: list[RetrievalCandidate] = Field(default_factory=list, max_length=30)
    missing_required_slots: list[MissingRequiredSlot] = Field(default_factory=list)
