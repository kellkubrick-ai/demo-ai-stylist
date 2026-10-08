from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from ai_stylist.schemas.base import Schema

OutfitRole = Literal["top", "bottom", "dress", "set", "outer_layer", "shoes", "bag", "accessory"]
ROLES = ("top", "bottom", "dress", "set", "outer_layer", "shoes", "bag", "accessory")


class StylingBudget(Schema):
    currency: str = "RUB"
    max_outfit_price: Decimal | None = Field(default=None, ge=0)
    min_item_price: Decimal | None = Field(default=None, ge=0)
    max_item_price: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if (
            self.min_item_price is not None
            and self.max_item_price is not None
            and self.min_item_price > self.max_item_price
        ):
            raise ValueError("Minimum item price exceeds maximum")
        return self


class StylingIntent(Schema):
    occasion: str | None = None
    styles: list[str] = Field(default_factory=list)
    formality: str | None = None
    product_types: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    budget: StylingBudget | None = None
    sizes_by_role: dict[OutfitRole, list[str]] = Field(default_factory=dict)
    user_context: dict[str, str] = Field(default_factory=dict)
    fit_preferences: list[str] = Field(default_factory=list)
    figure_preferences: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None

    @model_validator(mode="after")
    def validate_clarification(self) -> Self:
        if self.needs_clarification and not (self.clarification_question or "").strip():
            raise ValueError("Clarification requires a question")
        if not self.needs_clarification and self.clarification_question:
            raise ValueError("Question requires needs_clarification")
        if any(
            not sizes or any(not s.strip() for s in sizes) for sizes in self.sizes_by_role.values()
        ):
            raise ValueError("Requested role sizes must be nonempty strings")
        return self
