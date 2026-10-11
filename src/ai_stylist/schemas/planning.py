from typing import Self

from pydantic import Field, model_validator

from ai_stylist.schemas.base import Schema
from ai_stylist.schemas.intent import OutfitRole, StylingIntent

ROLE_TYPES: dict[str, tuple[str, ...]] = {
    "top": ("top", "shirt", "blouse", "t_shirt", "sweater", "cardigan", "hoodie"),
    "bottom": ("trousers", "jeans", "skirt", "shorts"),
    "dress": ("dress", "jumpsuit"),
    "set": ("suit_set", "set"),
    "outer_layer": ("blazer", "jacket", "coat", "trench", "vest", "cardigan"),
    "shoes": ("shoes", "sneakers", "loafers", "boots", "sandals", "pumps", "flats"),
    "bag": ("bag", "backpack", "clutch"),
    "accessory": ("belt", "scarf", "hat", "jewelry", "accessory"),
}
PRODUCT_TYPES = sorted({item for types in ROLE_TYPES.values() for item in types})


class OutfitSlot(Schema):
    role: OutfitRole
    required: bool = True
    product_type_constraints: list[str] = Field(default_factory=list)
    retrieval_query: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_types(self) -> Self:
        if not set(self.product_type_constraints) <= set(ROLE_TYPES[self.role]):
            raise ValueError("Product types must be supported for the slot role")
        return self


class OutfitComposition(Schema):
    plan_id: str = Field(min_length=1)
    anchor_focused: bool = False
    slots: list[OutfitSlot] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_composition(self) -> Self:
        roles = [slot.role for slot in self.slots]
        if len(roles) != len(set(roles)):
            raise ValueError("Roles must be unique in a composition")
        required = {slot.role for slot in self.slots if slot.required}
        if self.anchor_focused:
            if not required:
                raise ValueError("Anchor-focused composition requires a required anchor role")
        else:
            bases = ({"top", "bottom", "shoes"}, {"dress", "shoes"}, {"set", "shoes"})
            if not any(base <= required for base in bases):
                raise ValueError("Composition requires a complete base and shoes")
        if ("dress" in roles or "set" in roles) and ({"top", "bottom"} & set(roles)):
            raise ValueError("Do not combine a one-piece base with separate top/bottom slots")
        if "dress" in roles and "set" in roles:
            raise ValueError("Choose one base per composition")
        return self


class OutfitPlan(Schema):
    compositions: list[OutfitComposition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        ids = [p.plan_id for p in self.compositions]
        if len(ids) != len(set(ids)):
            raise ValueError("Plan IDs must be unique")
        return self


class StylingUnderstanding(Schema):
    intent: StylingIntent
    outfit_plan: OutfitPlan
    dialogue_reply: str | None = None

    @model_validator(mode="after")
    def require_plan(self) -> Self:
        if self.dialogue_reply is not None:
            if not self.dialogue_reply.strip():
                raise ValueError("Dialogue reply must be nonempty")
            if self.intent.needs_clarification or self.outfit_plan.compositions:
                raise ValueError("Dialogue reply cannot request clarification or search")
        elif self.intent.needs_clarification:
            if self.outfit_plan.compositions:
                raise ValueError("Clarification requires an empty outfit plan")
        elif not self.outfit_plan.compositions:
            raise ValueError("A meaningful request requires at least one composition")
        return self
