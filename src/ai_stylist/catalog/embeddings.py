import json


def embedding_text(record) -> str:
    group = record.product
    title = group.title.strip().rstrip(".")
    if group.color and group.color.casefold() not in title.casefold():
        title += f" в оттенке «{group.color.strip()}»"
    if group.brand:
        title += f" от {group.brand.strip()}"

    sentences = [title, group.description]
    details = group.catalog_attributes.get("features", [])
    details = details if isinstance(details, list) else []
    occasions = []
    if record.enrichment:
        enrichment = record.enrichment
        sentences.append(enrichment.enriched_description)
        details = [
            *details,
            enrichment.silhouette,
            enrichment.fit,
            enrichment.texture,
            enrichment.visual_weight,
            enrichment.formality,
            *enrichment.color_characteristics,
            *enrichment.styles,
            *enrichment.layering_suitability,
        ]
        occasions = enrichment.occasions
    descriptions = list(
        dict.fromkeys(
            value.strip() for value in details if isinstance(value, str) and value.strip()
        )
    )
    if descriptions:
        sentences.append(", ".join(descriptions))
    if occasions:
        sentences.append("Подходит для " + ", ".join(occasions))
    parts = [
        sentence.strip().rstrip(".") for sentence in sentences if sentence and sentence.strip()
    ]
    return ". ".join(part[0].upper() + part[1:] for part in parts) + "."


async def rebuild_embeddings(
    repository, embedder, report_path, *, force=False, limit=None, product_ids=None
):
    counts = {"embedded": 0, "reused": 0, "failed": 0}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    async for record in repository.iter_products():
        if product_ids is not None and record.product.id not in product_ids:
            continue
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
