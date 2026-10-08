import json

from sqlalchemy import func, select, update

from ai_stylist.catalog import tables as t
from ai_stylist.catalog.mapping import stable_id
from ai_stylist.catalog.repository import upsert, utcnow
from ai_stylist.schemas.catalog import CatalogCategory, CatalogProduct


async def export_snapshot(repository, path, dimensions: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    async with repository.engine.connect() as connection:
        await connection.execution_options(isolation_level="REPEATABLE READ")
        state = (await connection.execute(select(t.catalog_state))).mappings().first()
        if not state:
            raise ValueError("Import a catalog before exporting a snapshot")
        categories = (await connection.execute(select(t.categories))).mappings().all()
        header = {
            "snapshot_format": 1,
            "embedding_dimensions": dimensions,
            "version": state["version"],
            "source": state["source"],
            "manifest": state["manifest"],
            "categories": [dict(row) for row in categories],
            "product_count": await connection.scalar(
                select(func.count()).select_from(t.product_groups)
            ),
        }
        with path.open("x", encoding="utf-8") as output:
            output.write(json.dumps(header, ensure_ascii=False) + "\n")
            after = None
            while True:
                query = select(t.product_groups.c.id).order_by(t.product_groups.c.id).limit(100)
                if after:
                    query = query.where(t.product_groups.c.id > after)
                ids = list((await connection.execute(query)).scalars())
                if not ids:
                    break
                for record in await repository.get_products(ids, connection=connection):
                    product_id = record.product.id
                    embedding = (
                        (
                            await connection.execute(
                                select(t.product_embeddings).where(
                                    t.product_embeddings.c.product_group_id == product_id,
                                )
                            )
                        )
                        .mappings()
                        .first()
                    )
                    enrichment_model = await connection.scalar(
                        select(t.product_enrichments.c.model_id).where(
                            t.product_enrichments.c.product_group_id == product_id
                        )
                    )
                    images = (
                        (
                            await connection.execute(
                                select(t.product_images).where(
                                    t.product_images.c.product_group_id == product_id,
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    row = {
                        "record": record.model_dump(mode="json"),
                        "enrichment_model": enrichment_model,
                        "images": [
                            {"url": image["url"], "is_usable": image["is_usable"]}
                            for image in images
                        ],
                        "embedding": {
                            "model": embedding["embedding_model"],
                            "vector": [float(v) for v in embedding["embedding"]],
                        }
                        if embedding
                        else None,
                    }
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")
                after = ids[-1]
            output.write(json.dumps({"snapshot_complete": True}) + "\n")
    return {"path": str(path), "version": header["version"]}


async def restore_snapshot(repository, path, dimensions: int):
    with path.open(encoding="utf-8") as source:
        header = json.loads(next(source))
        if header.get("snapshot_format") != 1:
            raise ValueError("Unsupported catalog snapshot format")
        if header["embedding_dimensions"] != dimensions:
            raise ValueError("Snapshot embedding dimension differs from configured database")
        async with repository.engine.begin() as connection:
            existing = await connection.scalar(select(t.product_groups.c.id).limit(1))
            if existing:
                raise ValueError(
                    "Snapshot restore requires an empty catalog; nothing was overwritten"
                )
            categories = [CatalogCategory.model_validate(c) for c in header["categories"]]
            await repository.import_categories(connection, categories)
            count = 0
            complete = False
            for line in source:
                row = json.loads(line)
                if row.get("snapshot_complete") is True:
                    complete = True
                    if source.read().strip():
                        raise ValueError("Unexpected data after snapshot completion marker")
                    break
                record = CatalogProduct.model_validate(row["record"])
                count += 1
                product_id = record.product.id
                await repository.import_product(connection, record, first=True)
                await repository.sync_images(
                    connection,
                    {
                        product_id: [str(url) for url in record.product.image_urls],
                    },
                )
                for image in row["images"]:
                    await connection.execute(
                        update(t.product_images)
                        .where(
                            t.product_images.c.product_group_id == product_id,
                            t.product_images.c.url == image["url"],
                        )
                        .values(is_usable=image["is_usable"])
                    )
                if record.enrichment:
                    await upsert(
                        connection,
                        t.product_enrichments,
                        {
                            "id": stable_id("enrichment", product_id),
                            "product_group_id": product_id,
                            **record.enrichment.model_dump(mode="json"),
                            "model_id": row["enrichment_model"],
                            "updated_at": utcnow(),
                        },
                        conflict="product_group_id",
                    )
                if embedding := row["embedding"]:
                    if len(embedding["vector"]) != dimensions:
                        raise ValueError("Snapshot contains an invalid vector dimension")
                    await upsert(
                        connection,
                        t.product_embeddings,
                        {
                            "product_group_id": product_id,
                            "embedding": embedding["vector"],
                            "embedding_model": embedding["model"],
                            "updated_at": utcnow(),
                        },
                        conflict="product_group_id",
                    )
            if not complete or count != header["product_count"]:
                raise ValueError("Incomplete catalog snapshot; restore rolled back")
            await repository.finish_import(
                connection, header["version"], header["source"], header["manifest"]
            )
    return {"version": header["version"], "restored": True}
