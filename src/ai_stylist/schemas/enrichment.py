from typing import Any

from pydantic import Field

from ai_stylist.schemas.base import Schema


class ProductEnrichment(Schema):
    styles: list[str] = Field(default_factory=list)
    silhouette: str | None = None
    fit: str | None = None
    formality: str | None = None
    occasions: list[str] = Field(default_factory=list)
    texture: str | None = None
    visual_weight: str | None = None
    color_characteristics: list[str] = Field(default_factory=list)
    layering_suitability: list[str] = Field(default_factory=list)
    enriched_description: str | None = None


class EnrichmentRequest(Schema):
    product_id: str
    title: str
    description: str | None = None
    product_type: str | None = None
    source_category_names: list[str] = Field(default_factory=list)
    color: str | None = None
    catalog_attributes: dict[str, Any] = Field(default_factory=dict)
    image_urls: list[str] = Field(min_length=1, max_length=2)
