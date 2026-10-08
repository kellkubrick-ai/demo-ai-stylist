"""Frozen initial demo schema, independent of later application metadata changes."""

from alembic import op

from ai_stylist.config import Settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = [
    """CREATE TABLE catalog_state (
    id SERIAL NOT NULL, 
    version TEXT NOT NULL, 
    source TEXT NOT NULL, 
    manifest JSONB NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
)""",
    """CREATE TABLE categories (
    id UUID NOT NULL, 
    external_id TEXT NOT NULL, 
    name TEXT NOT NULL, 
    parent_id UUID, 
    PRIMARY KEY (id), 
    UNIQUE (external_id), 
    FOREIGN KEY(parent_id) REFERENCES categories (id)
)""",
    """CREATE TABLE product_groups (
    id UUID NOT NULL, 
    external_id TEXT NOT NULL, 
    source_model_id TEXT, 
    title TEXT NOT NULL, 
    description TEXT, 
    brand TEXT, 
    product_type TEXT, 
    color TEXT, 
    catalog_attributes JSONB DEFAULT '{}' NOT NULL, 
    product_url TEXT, 
    source_updated_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (external_id)
)""",
    """CREATE TABLE styling_sessions (
    id UUID NOT NULL, 
    telegram_user_id BIGINT NOT NULL, 
    status TEXT NOT NULL, 
    current_intent JSONB, 
    current_outfit_plan JSONB, 
    dialogue_history JSONB DEFAULT '[]' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CHECK (status IN ('active', 'finished'))
)""",
    """CREATE TABLE offers (
    id UUID NOT NULL, 
    external_id TEXT NOT NULL, 
    product_group_id UUID NOT NULL, 
    sku TEXT, 
    barcode TEXT, 
    size TEXT, 
    price NUMERIC NOT NULL, 
    old_price NUMERIC, 
    currency VARCHAR(12) NOT NULL, 
    available BOOLEAN NOT NULL, 
    source_attributes JSONB DEFAULT '{}' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CHECK (price >= 0), 
    CHECK (old_price IS NULL OR old_price >= 0), 
    UNIQUE (external_id), 
    FOREIGN KEY(product_group_id) REFERENCES product_groups (id), 
    UNIQUE (sku)
)""",
    """CREATE TABLE product_categories (
    product_group_id UUID NOT NULL, 
    category_id UUID NOT NULL, 
    PRIMARY KEY (product_group_id, category_id), 
    FOREIGN KEY(product_group_id) REFERENCES product_groups (id), 
    FOREIGN KEY(category_id) REFERENCES categories (id)
)""",
    """CREATE TABLE product_embeddings (
    product_group_id UUID NOT NULL, 
    embedding VECTOR NOT NULL, 
    embedding_model TEXT NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (product_group_id), 
    FOREIGN KEY(product_group_id) REFERENCES product_groups (id)
)""",
    """CREATE TABLE product_enrichments (
    id UUID NOT NULL, 
    product_group_id UUID NOT NULL, 
    styles JSONB DEFAULT '[]' NOT NULL, 
    occasions JSONB DEFAULT '[]' NOT NULL, 
    color_characteristics JSONB DEFAULT '[]' NOT NULL, 
    layering_suitability JSONB DEFAULT '[]' NOT NULL, 
    silhouette TEXT, 
    fit TEXT, 
    formality TEXT, 
    texture TEXT, 
    visual_weight TEXT, 
    enriched_description TEXT, 
    model_id TEXT, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (product_group_id), 
    FOREIGN KEY(product_group_id) REFERENCES product_groups (id)
)""",
    """CREATE TABLE product_images (
    id UUID NOT NULL, 
    product_group_id UUID NOT NULL, 
    url TEXT NOT NULL, 
    position INTEGER NOT NULL, 
    is_primary BOOLEAN DEFAULT 'false' NOT NULL, 
    is_usable BOOLEAN, 
    PRIMARY KEY (id), 
    FOREIGN KEY(product_group_id) REFERENCES product_groups (id)
)""",
    """CREATE TABLE related_products (
    source_product_group_id UUID NOT NULL, 
    target_product_group_id UUID NOT NULL, 
    relation_type TEXT NOT NULL, 
    PRIMARY KEY (source_product_group_id, target_product_group_id, relation_type), 
    FOREIGN KEY(source_product_group_id) REFERENCES product_groups (id), 
    FOREIGN KEY(target_product_group_id) REFERENCES product_groups (id)
)""",
    """CREATE TABLE styling_runs (
    id UUID NOT NULL, 
    session_id UUID, 
    user_request TEXT NOT NULL, 
    catalog_version TEXT NOT NULL, 
    intent_json JSONB NOT NULL, 
    outfit_plan_json JSONB NOT NULL, 
    vlm_request_json JSONB, 
    result_json JSONB, 
    error TEXT, 
    metrics JSONB DEFAULT '{}' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(session_id) REFERENCES styling_sessions (id)
)""",
    """CREATE INDEX ix_product_type
ON product_groups (product_type)""",
    """CREATE INDEX ix_sessions_user
ON styling_sessions (telegram_user_id)""",
    """CREATE UNIQUE INDEX uq_active_session_user
ON styling_sessions (telegram_user_id) WHERE status = 'active'""",
    """CREATE INDEX ix_offers_available_size
ON offers (available, size)""",
    """CREATE INDEX ix_offers_product
ON offers (product_group_id)""",
    """CREATE INDEX ix_images_product
ON product_images (product_group_id)""",
]
DROP_TABLES = [
    "styling_runs",
    "related_products",
    "product_images",
    "product_enrichments",
    "product_embeddings",
    "product_categories",
    "offers",
    "styling_sessions",
    "product_groups",
    "categories",
    "catalog_state",
]


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    dimensions = Settings().embedding_dimensions
    for statement in DDL:
        # Configured once on a new database; runtime checks embedding dimensions.
        statement = statement.replace(
            "embedding VECTOR NOT NULL", f"embedding VECTOR({dimensions}) NOT NULL"
        )
        op.execute(statement)


def downgrade():
    for name in DROP_TABLES:
        op.execute(f'DROP TABLE "{name}"')
