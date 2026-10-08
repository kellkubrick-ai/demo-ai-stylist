from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()


def timestamps():
    return [
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ]


product_groups = Table(
    "product_groups",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("external_id", Text, unique=True, nullable=False),
    Column("source_model_id", Text),
    Column("title", Text, nullable=False),
    Column("description", Text),
    Column("brand", Text),
    Column("product_type", Text),
    Column("color", Text),
    Column("catalog_attributes", JSONB, nullable=False, server_default="{}"),
    Column("product_url", Text),
    Column("source_updated_at", DateTime(timezone=True)),
    *timestamps(),
)
offers = Table(
    "offers",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("external_id", Text, unique=True, nullable=False),
    Column("product_group_id", ForeignKey("product_groups.id"), nullable=False),
    Column("sku", Text),
    Column("barcode", Text),
    Column("size", Text),
    Column("price", Numeric, nullable=False),
    Column("old_price", Numeric),
    Column("currency", String(12), nullable=False),
    Column("available", Boolean, nullable=False),
    Column("source_attributes", JSONB, nullable=False, server_default="{}"),
    CheckConstraint("price >= 0"),
    CheckConstraint("old_price IS NULL OR old_price >= 0"),
    *timestamps(),
)
categories = Table(
    "categories",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("external_id", Text, unique=True, nullable=False),
    Column("name", Text, nullable=False),
    Column("parent_id", ForeignKey("categories.id")),
)
product_categories = Table(
    "product_categories",
    metadata,
    Column("product_group_id", ForeignKey("product_groups.id"), primary_key=True),
    Column("category_id", ForeignKey("categories.id"), primary_key=True),
)
product_images = Table(
    "product_images",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("product_group_id", ForeignKey("product_groups.id"), nullable=False),
    Column("url", Text, nullable=False),
    Column("position", Integer, nullable=False),
    Column("is_primary", Boolean, nullable=False, server_default="false"),
    Column("is_usable", Boolean),
)
product_enrichments = Table(
    "product_enrichments",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("product_group_id", ForeignKey("product_groups.id"), unique=True, nullable=False),
    *[
        Column(name, JSONB, nullable=False, server_default="[]")
        for name in ("styles", "occasions", "color_characteristics", "layering_suitability")
    ],
    *[
        Column(name, Text)
        for name in (
            "silhouette",
            "fit",
            "formality",
            "texture",
            "visual_weight",
            "enriched_description",
        )
    ],
    Column("model_id", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
# The initial migration replaces this with the configured dimension.
product_embeddings = Table(
    "product_embeddings",
    metadata,
    Column("product_group_id", ForeignKey("product_groups.id"), primary_key=True),
    Column("embedding", Vector(), nullable=False),
    Column("embedding_model", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
related_products = Table(
    "related_products",
    metadata,
    Column("source_product_group_id", ForeignKey("product_groups.id"), primary_key=True),
    Column("target_product_group_id", ForeignKey("product_groups.id"), primary_key=True),
    Column("relation_type", Text, primary_key=True),
)
catalog_state = Table(
    "catalog_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("version", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
styling_sessions = Table(
    "styling_sessions",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("telegram_user_id", BigInteger, nullable=False),
    Column("status", Text, nullable=False),
    Column("current_intent", JSONB),
    Column("current_outfit_plan", JSONB),
    Column("dialogue_history", JSONB, nullable=False, server_default="[]"),
    CheckConstraint("status IN ('active', 'finished')"),
    *timestamps(),
)
styling_runs = Table(
    "styling_runs",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("session_id", ForeignKey("styling_sessions.id")),
    Column("user_request", Text, nullable=False),
    Column("catalog_version", Text, nullable=False),
    Column("intent_json", JSONB, nullable=False),
    Column("outfit_plan_json", JSONB, nullable=False),
    Column("vlm_request_json", JSONB),
    Column("result_json", JSONB),
    Column("error", Text),
    Column("metrics", JSONB, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_product_type", product_groups.c.product_type)
Index("ix_offers_product", offers.c.product_group_id)
Index("ix_offers_available_size", offers.c.available, offers.c.size)
Index("ix_images_product", product_images.c.product_group_id)
Index("ix_sessions_user", styling_sessions.c.telegram_user_id)
Index(
    "uq_active_session_user",
    styling_sessions.c.telegram_user_id,
    unique=True,
    postgresql_where=styling_sessions.c.status == "active",
)
