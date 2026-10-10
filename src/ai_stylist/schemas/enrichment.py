from typing import Any

from pydantic import Field

from ai_stylist.schemas.base import Schema


class ProductEnrichment(Schema):
    styles: list[str] = Field(
        default_factory=list,
        description="Zero to four concise visual style labels; not occasions or construction.",
    )
    silhouette: str | None = Field(
        default=None,
        description=(
            "Visible shape, proportions, and draping only; null when the photo does not support "
            "a reliable description."
        ),
    )
    fit: str | None = Field(
        default=None,
        description=(
            "Visible cut of the item itself, never a prediction of fit on a future user; null "
            "when not visible or not applicable."
        ),
    )
    formality: str | None = Field(
        default=None,
        description="One concise visual formality assessment; null when uncertain.",
    )
    occasions: list[str] = Field(
        default_factory=list,
        description=(
            "Zero to four plausible use situations inferred from appearance and catalog categories."
        ),
    )
    texture: str | None = Field(
        default=None,
        description=(
            "Visible surface character only, without inferring hidden fiber composition; null "
            "when uncertain."
        ),
    )
    visual_weight: str | None = Field(
        default=None,
        description="Exactly one of: лёгкий, средний, тяжёлый; null when uncertain.",
    )
    color_characteristics: list[str] = Field(
        default_factory=list,
        description="Zero to four visible color and tonal characteristics.",
    )
    layering_suitability: list[str] = Field(
        default_factory=list,
        description=(
            "Zero to three observations about visual layering role and compatibility, never "
            "claims about physical room, warmth, stretch, or wearability; empty when not "
            "applicable."
        ),
    )
    enriched_description: str | None = Field(
        default=None,
        description=(
            "Two or three factual Russian sentences combining supported catalog facts and visible "
            "attributes without sales language."
        ),
    )


class EnrichmentRequest(Schema):
    product_id: str
    title: str
    description: str | None = None
    product_type: str | None = None
    source_category_names: list[str] = Field(default_factory=list)
    color: str | None = None
    catalog_attributes: dict[str, Any] = Field(default_factory=dict)
    image_urls: list[str] = Field(min_length=1, max_length=2)
