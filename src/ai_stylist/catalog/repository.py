from collections import defaultdict
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ai_stylist.catalog import tables as t
from ai_stylist.catalog.mapping import stable_id
from ai_stylist.retriever.query_builder import slot_filters
from ai_stylist.schemas.catalog import CatalogCategory, CatalogProduct, Offer, ProductGroup
from ai_stylist.schemas.enrichment import ProductEnrichment


def utcnow():
    return datetime.now(UTC)


async def upsert(connection, table, data: dict, conflict: str = "id"):
    statement = pg_insert(table).values(**data)
    changes = {key: statement.excluded[key] for key in data if key not in (conflict, "created_at")}
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=[table.c[conflict]],
            set_=changes,
        )
    )


class CatalogRepository:
    def __init__(self, engine):
        self.engine = engine

    async def import_categories(self, connection, categories: list[CatalogCategory]):
        for category in categories:
            await upsert(
                connection,
                t.categories,
                {
                    **category.model_dump(),
                    "parent_id": None,
                },
            )
        for category in categories:
            await connection.execute(
                update(t.categories)
                .where(t.categories.c.id == category.id)
                .values(parent_id=category.parent_id)
            )

    async def begin_import(self, connection):
        # Full feed refresh: missing/invalid offers cannot remain purchasable.
        await connection.execute(update(t.offers).values(available=False))
        await connection.execute(delete(t.product_categories))

    async def invalidate_visual(self, connection, product_id):
        await connection.execute(
            delete(t.product_enrichments).where(
                t.product_enrichments.c.product_group_id == product_id,
            )
        )
        await connection.execute(
            delete(t.product_embeddings).where(
                t.product_embeddings.c.product_group_id == product_id,
            )
        )

    async def import_product(self, connection, record: CatalogProduct, first: bool):
        group = record.product
        if first:
            data = group.model_dump(mode="json", exclude={"source_categories", "image_urls"})
            existing = (
                (
                    await connection.execute(
                        select(t.product_groups).where(
                            t.product_groups.c.id == group.id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            visual_fields = (
                "title",
                "description",
                "brand",
                "color",
                "product_type",
                "catalog_attributes",
            )
            if existing and any(existing[key] != data[key] for key in visual_fields):
                await self.invalidate_visual(connection, group.id)
            await upsert(connection, t.product_groups, {**data, "updated_at": utcnow()})
        for category in group.source_categories:
            statement = (
                pg_insert(t.product_categories)
                .values(
                    product_group_id=group.id,
                    category_id=category.id,
                )
                .on_conflict_do_nothing()
            )
            await connection.execute(statement)
        for offer in record.offers:
            data = offer.model_dump()
            await upsert(connection, t.offers, {**data, "updated_at": utcnow()})

    async def sync_images(self, connection, grouped_images: dict[str, list[str]]):
        for product_id, urls in grouped_images.items():
            existing = (
                (
                    await connection.execute(
                        select(t.product_images)
                        .where(
                            t.product_images.c.product_group_id == product_id,
                        )
                        .order_by(t.product_images.c.position)
                    )
                )
                .mappings()
                .all()
            )
            old_urls = [row["url"] for row in existing]
            if existing and old_urls != urls:
                await self.invalidate_visual(connection, product_id)
            await connection.execute(
                delete(t.product_images).where(
                    t.product_images.c.product_group_id == product_id,
                    t.product_images.c.url.not_in(urls),
                )
            )
            usable = {row["url"]: row["is_usable"] for row in existing}
            for position, url in enumerate(urls):
                await upsert(
                    connection,
                    t.product_images,
                    {
                        "id": stable_id("image", product_id + ":" + url),
                        "product_group_id": product_id,
                        "url": url,
                        "position": position,
                        "is_primary": position == 0,
                        "is_usable": usable.get(url),
                    },
                )

    async def finish_import(self, connection, version: str, source: str, report: dict):
        await upsert(
            connection,
            t.catalog_state,
            {
                "id": 1,
                "version": version,
                "source": source,
                "manifest": report,
                "updated_at": utcnow(),
            },
        )

    async def version(self) -> str:
        async with self.engine.connect() as connection:
            version = await connection.scalar(select(t.catalog_state.c.version))
        return version or "uninitialized"

    async def audiences(self) -> list[str]:
        field = t.product_groups.c.catalog_attributes["audience"].as_string()
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(field)
                        .distinct()
                        .where(
                            field.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return sorted(rows)

    async def get_products(
        self, ids: list[str], *, usable_images_only=False, connection=None
    ) -> list[CatalogProduct]:
        if not ids:
            return []
        async with (
            self.engine.connect() if connection is None else nullcontext(connection) as connection
        ):
            groups = (
                (
                    await connection.execute(
                        select(t.product_groups).where(
                            t.product_groups.c.id.in_(ids),
                        )
                    )
                )
                .mappings()
                .all()
            )
            offer_rows = (
                (
                    await connection.execute(
                        select(t.offers)
                        .where(
                            t.offers.c.product_group_id.in_(ids),
                        )
                        .order_by(t.offers.c.id)
                    )
                )
                .mappings()
                .all()
            )
            query = select(t.product_images).where(t.product_images.c.product_group_id.in_(ids))
            if usable_images_only:
                query = query.where(t.product_images.c.is_usable.is_(True))
            images = (
                (
                    await connection.execute(
                        query.order_by(t.product_images.c.position, t.product_images.c.id)
                    )
                )
                .mappings()
                .all()
            )
            category_rows = (
                (
                    await connection.execute(
                        select(
                            t.categories,
                            t.product_categories.c.product_group_id,
                        )
                        .join(t.product_categories)
                        .where(
                            t.product_categories.c.product_group_id.in_(ids),
                        )
                    )
                )
                .mappings()
                .all()
            )
            enriched = (
                (
                    await connection.execute(
                        select(t.product_enrichments).where(
                            t.product_enrichments.c.product_group_id.in_(ids),
                        )
                    )
                )
                .mappings()
                .all()
            )
        offers_by_group = defaultdict(list)
        images_by_group = defaultdict(list)
        categories_by_group = defaultdict(list)
        for row in offer_rows:
            offers_by_group[row["product_group_id"]].append(
                Offer.model_validate({key: row[key] for key in Offer.model_fields})
            )
        for row in images:
            images_by_group[row["product_group_id"]].append(row["url"])
        for row in category_rows:
            categories_by_group[row["product_group_id"]].append(
                CatalogCategory.model_validate(
                    {key: row[key] for key in CatalogCategory.model_fields}
                )
            )
        enrichments = {
            row["product_group_id"]: ProductEnrichment.model_validate(
                {key: row[key] for key in ProductEnrichment.model_fields}
            )
            for row in enriched
        }
        records = {}
        for row in groups:
            group = ProductGroup.model_validate(
                {
                    **{
                        key: row[key]
                        for key in ProductGroup.model_fields
                        if key not in ("source_categories", "image_urls")
                    },
                    "source_categories": categories_by_group[row["id"]],
                    "image_urls": images_by_group[row["id"]],
                }
            )
            records[group.id] = CatalogProduct(
                product=group,
                offers=offers_by_group[group.id],
                enrichment=enrichments.get(group.id),
            )
        return [records[product_id] for product_id in ids if product_id in records]

    async def iter_products(self, *, eligible_only=True, batch_size=100):
        after = None
        while True:
            query = select(t.product_groups.c.id).order_by(t.product_groups.c.id).limit(batch_size)
            if after:
                query = query.where(t.product_groups.c.id > after)
            if eligible_only:
                query = query.where(
                    t.product_groups.c.product_type.is_not(None),
                    select(t.offers.c.id)
                    .where(
                        t.offers.c.product_group_id == t.product_groups.c.id,
                        t.offers.c.available.is_(True),
                    )
                    .exists(),
                    select(t.product_images.c.id)
                    .where(
                        t.product_images.c.product_group_id == t.product_groups.c.id,
                    )
                    .exists(),
                )
            async with self.engine.connect() as connection:
                ids = list((await connection.execute(query)).scalars())
            if not ids:
                break
            for product in await self.get_products(ids):
                yield product
            after = ids[-1]

    async def mark_image(self, product_id: str, url: str, usable: bool):
        async with self.engine.begin() as connection:
            await connection.execute(
                update(t.product_images)
                .where(
                    t.product_images.c.product_group_id == product_id,
                    t.product_images.c.url == url,
                )
                .values(is_usable=usable)
            )

    async def save_enrichment(self, product_id: str, result: ProductEnrichment, model: str):
        async with self.engine.begin() as connection:
            await upsert(
                connection,
                t.product_enrichments,
                {
                    "id": stable_id("enrichment", product_id),
                    "product_group_id": product_id,
                    **result.model_dump(mode="json"),
                    "model_id": model,
                    "updated_at": utcnow(),
                },
                conflict="product_group_id",
            )
            await connection.execute(
                delete(t.product_embeddings).where(
                    t.product_embeddings.c.product_group_id == product_id,
                )
            )

    async def has_embedding(self, product_id: str, model: str) -> bool:
        async with self.engine.connect() as connection:
            return bool(
                await connection.scalar(
                    select(t.product_embeddings.c.product_group_id).where(
                        t.product_embeddings.c.product_group_id == product_id,
                        t.product_embeddings.c.embedding_model == model,
                    )
                )
            )

    async def save_embedding(self, product_id: str, vector: list[float], model: str):
        async with self.engine.begin() as connection:
            await upsert(
                connection,
                t.product_embeddings,
                {
                    "product_group_id": product_id,
                    "embedding": vector,
                    "embedding_model": model,
                    "updated_at": utcnow(),
                },
                conflict="product_group_id",
            )

    async def rank_slot(self, intent, slot, vector: list[float], model: str, limit=30):
        distance = t.product_embeddings.c.embedding.cosine_distance(vector)
        query = (
            select(t.product_groups.c.id, distance.label("distance"))
            .join(
                t.product_embeddings,
            )
            .where(
                *slot_filters(intent, slot),
                t.product_embeddings.c.embedding_model == model,
            )
            .order_by(distance, t.product_groups.c.id)
            .limit(limit)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).all()
        records = await self.get_products([row.id for row in rows], usable_images_only=True)
        scores = {row.id: 1.0 - float(row.distance) for row in rows}
        return [(record, scores[record.product.id]) for record in records]

    async def save_run(self, data: dict):
        async with self.engine.begin() as connection:
            await connection.execute(insert(t.styling_runs).values(**data))

    async def get_run(self, run_id: str) -> dict:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(t.styling_runs).where(
                            t.styling_runs.c.id == run_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)

    async def find_session(self, user_id: int):
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(t.styling_sessions).where(
                            t.styling_sessions.c.telegram_user_id == user_id,
                            t.styling_sessions.c.status == "active",
                        )
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def active_session(self, user_id: int) -> dict:
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(t.styling_sessions).where(
                            t.styling_sessions.c.telegram_user_id == user_id,
                            t.styling_sessions.c.status == "active",
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row:
                return dict(row)
            data = {
                "id": str(uuid4()),
                "telegram_user_id": user_id,
                "status": "active",
                "dialogue_history": [],
            }
            await connection.execute(insert(t.styling_sessions).values(**data))
        return data

    async def update_session(self, session_id: str, **data):
        async with self.engine.begin() as connection:
            await connection.execute(
                update(t.styling_sessions)
                .where(
                    t.styling_sessions.c.id == session_id,
                )
                .values(**data, updated_at=utcnow())
            )

    async def report(self) -> dict:
        async with self.engine.connect() as connection:
            products = await connection.scalar(select(func.count()).select_from(t.product_groups))
            offers = await connection.scalar(select(func.count()).select_from(t.offers))
            available = await connection.scalar(
                select(func.count())
                .select_from(t.offers)
                .where(
                    t.offers.c.available.is_(True),
                )
            )
            enriched = await connection.scalar(
                select(func.count()).select_from(t.product_enrichments)
            )
            embeddings = await connection.scalar(
                select(func.count()).select_from(t.product_embeddings)
            )
            usable = await connection.scalar(
                select(func.count())
                .select_from(t.product_images)
                .where(
                    t.product_images.c.is_usable.is_(True),
                )
            )
            types = (
                await connection.execute(
                    select(
                        t.product_groups.c.product_type,
                        func.count(),
                    ).group_by(t.product_groups.c.product_type)
                )
            ).all()
            missing_images = await connection.scalar(
                select(func.count())
                .select_from(t.product_groups)
                .where(
                    ~select(t.product_images.c.id)
                    .where(
                        t.product_images.c.product_group_id == t.product_groups.c.id,
                    )
                    .exists(),
                )
            )
            rejected_images = await connection.scalar(
                select(func.count())
                .select_from(t.product_images)
                .where(
                    t.product_images.c.is_usable.is_(False),
                )
            )
            sizes = (
                await connection.execute(
                    select(t.product_groups.c.product_type, t.offers.c.size)
                    .select_from(t.offers.join(t.product_groups))
                    .where(t.offers.c.available.is_(True), t.offers.c.size.is_not(None))
                    .distinct()
                    .order_by(t.product_groups.c.product_type, t.offers.c.size)
                )
            ).all()
            audience = t.product_groups.c.catalog_attributes["audience"].as_string()
            audiences = (
                await connection.execute(select(audience, func.count()).group_by(audience))
            ).all()
        sizes_by_type = {}
        for product_type, size in sizes:
            sizes_by_type.setdefault(product_type or "unmapped", []).append(size)
        return {
            "version": await self.version(),
            "products": products,
            "offers": offers,
            "available_offers": available,
            "enrichments": enriched,
            "embeddings": embeddings,
            "usable_images": usable,
            "rejected_images": rejected_images,
            "products_without_images": missing_images,
            "product_types": {key or "unmapped": count for key, count in types},
            "available_sizes_by_type": sizes_by_type,
            "audiences": {key or "unknown": count for key, count in audiences},
        }
