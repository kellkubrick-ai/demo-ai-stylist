"""Store source article values even when several size offers share one article."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("offers_sku_key", "offers", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("offers_sku_key", "offers", ["sku"])
