import json


def embedding_text(record) -> str:
    group = record.product
    return json.dumps(
        {
            "title": group.title,
            "original_description": group.description,
            "product_type": group.product_type,
            "color": group.color,
            "brand": group.brand,
            "catalog_attributes": group.catalog_attributes,
            "source_categories": [c.name for c in group.source_categories],
            "inferred_styling_attributes": record.enrichment.model_dump()
            if record.enrichment
            else None,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


async def rebuild_embeddings(repository, embedder, report_path, *, force=False, limit=None):
    counts = {"embedded": 0, "reused": 0, "failed": 0}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    async for record in repository.iter_products():
        error = None
        try:
            if not force and await repository.has_embedding(record.product.id, embedder.model):
                status = "reused"
            else:
                vector = await embedder.embed(embedding_text(record))
                await repository.save_embedding(record.product.id, vector, embedder.model)
                status = "embedded"
        except Exception as exc:
            status, error = "failed", type(exc).__name__ + ": " + str(exc)
        counts[status] += 1
        with report_path.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "product_id": record.product.id,
                        "status": status,
                        "error": error,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        if limit is not None and sum(counts.values()) >= limit:
            break
    return counts
