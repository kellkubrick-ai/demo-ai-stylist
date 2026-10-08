from decimal import Decimal
from typing import Any, Self

from pydantic import Field, HttpUrl, model_validator

from ai_stylist.schemas.base import Schema
from ai_stylist.schemas.enrichment import ProductEnrichment


class CatalogCategory(Schema):
    id: str
    external_id: str
    name: str
    parent_id: str | None = None


class ProductGroup(Schema):
    id: str
    external_id: str
    source_model_id: str | None = None
    title: str
    description: str | None = None
    brand: str | None = None
    product_type: str | None = None
    source_categories: list[CatalogCategory] = Field(default_factory=list)
    color: str | None = None
    catalog_attributes: dict[str, Any] = Field(default_factory=dict)
    product_url: HttpUrl | None = None
    image_urls: list[HttpUrl] = Field(default_factory=list)


class Offer(Schema):
    id: str
    external_id: str
    sku: str | None = None
    barcode: str | None = None
    product_group_id: str
    size: str | None = None
    price: Decimal = Field(ge=0)
    old_price: Decimal | None = Field(default=None, ge=0)
    currency: str
    available: bool
    source_attributes: dict[str, Any] = Field(default_factory=dict)


class CatalogProduct(Schema):
    product: ProductGroup
    offers: list[Offer]
    enrichment: ProductEnrichment | None = None

    @model_validator(mode="after")
    def validate_offer_links(self) -> Self:
        if any(offer.product_group_id != self.product.id for offer in self.offers):
            raise ValueError("Every offer must belong to its visual product")
        return self
