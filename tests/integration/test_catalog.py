from pathlib import Path
from xml.etree.ElementTree import ParseError

import pytest
from sqlalchemy import select, update

from ai_stylist.catalog import tables as t
from ai_stylist.catalog.importer import import_feed
from ai_stylist.catalog.mapping import FeedMapping
from ai_stylist.catalog.snapshot import export_snapshot, restore_snapshot
from ai_stylist.retriever.service import Retriever
from ai_stylist.schemas.enrichment import ProductEnrichment
from ai_stylist.schemas.intent import StylingIntent
from ai_stylist.schemas.retrieval import RetrievalRequest

FIXTURES = Path(__file__).parents[1] / "fixtures"
pytestmark = pytest.mark.integration


async def prepare(repository, mapping):
    report = await import_feed(repository, FIXTURES / "synthetic.xml", mapping, "synthetic-only")
    records = [r async for r in repository.iter_products()]
    for record in records:
        for url in record.product.image_urls:
            await repository.mark_image(record.product.id, str(url), True)
        await repository.save_enrichment(
            record.product.id, ProductEnrichment(styles=["minimal"]), "test-enrichment"
        )
        await repository.save_embedding(record.product.id, [1.0, 0.0, 0.0], "test-embedding")
    return report


async def test_import_is_idempotent_and_preserves_enrichment(repository, mapping):
    first = await prepare(repository, mapping)
    assert first["offers"] == 11 and first["products"] == 8
    second = await import_feed(repository, FIXTURES / "synthetic.xml", mapping, "synthetic-only")
    assert second["version"] == first["version"]
    report = await repository.report()
    assert report["products"] == 8 and report["offers"] == 11
    assert report["products_without_images"] == 0 and report["rejected_images"] == 0
    assert report["available_sizes_by_type"]["shoes"] == ["38", "39"]
    assert report["enrichments"] == report["embeddings"] == 8
    records = [r async for r in repository.iter_products()]
    black = next(r for r in records if r.product.external_id == "top-01:Чёрный")
    assert len(black.offers) == 2
    assert len(black.product.source_categories) == 2
    assert black.enrichment.styles == ["minimal"]


async def test_offer_only_changes_do_not_invalidate_visual_enrichment(
    repository, mapping, tmp_path
):
    await prepare(repository, mapping)
    source = (FIXTURES / "synthetic.xml").read_text(encoding="utf-8")
    updated = tmp_path / "price.xml"
    updated.write_text(source.replace("100.10", "99.99"), encoding="utf-8")
    await import_feed(repository, updated, mapping, "synthetic-updated")
    assert (await repository.report())["enrichments"] == 8
    updated.write_text(source.replace("top-black.png", "top-black-new.png"), encoding="utf-8")
    await import_feed(repository, updated, mapping, "synthetic-updated")
    report = await repository.report()
    assert report["enrichments"] == 7 and report["embeddings"] == 7


async def test_sql_filters_and_pgvector_preserve_complete_pool(repository, mapping, plan):
    await prepare(repository, mapping)

    class Embedder:
        model = "test-embedding"

        async def embed(self, text):
            return [1.0, 0.0, 0.0]

    intent = StylingIntent(colors=["Черный"], sizes_by_role={"top": ["M"], "shoes": ["38"]})
    pool = await Retriever(repository, Embedder()).retrieve(
        RetrievalRequest(
            intent=intent,
            outfit_plan=plan,
            max_products=3,
        )
    )
    assert len(pool.products) == 3 and not pool.missing_required_slots
    shoes = next(c for c in pool.products if "shoes" in c.eligible_slots)
    assert [o.size for o in shoes.product.offers] == ["38"]


async def test_sql_avoid_uses_normalized_verified_features(repository, mapping, plan):
    await prepare(repository, mapping)
    records = [r async for r in repository.iter_products()]
    black = next(r for r in records if r.product.external_id == "top-01:Чёрный")
    async with repository.engine.begin() as connection:
        await connection.execute(
            update(t.product_groups)
            .where(
                t.product_groups.c.id == black.product.id,
            )
            .values(catalog_attributes={"features": ["  КАБЛУКИ  "]})
        )
    ranked = await repository.rank_slot(
        StylingIntent(avoid=["каблуки"]),
        plan.compositions[0].slots[0],
        [1.0, 0.0, 0.0],
        "test-embedding",
        30,
    )
    assert ranked and black.product.id not in {record.product.id for record, _ in ranked}


async def test_snapshot_roundtrip_is_reproducible_and_refuses_overwrite(
    repository, mapping, tmp_path
):
    await prepare(repository, mapping)
    snapshot = tmp_path / "catalog.jsonl"
    exported = await export_snapshot(repository, snapshot, 3)
    with pytest.raises(ValueError, match="empty catalog"):
        await restore_snapshot(repository, snapshot, 3)
    # Only clear this test-owned schema. Production restore never removes existing data.
    async with repository.engine.begin() as connection:
        for table in reversed(t.metadata.sorted_tables):
            await connection.execute(table.delete())
    restored = await restore_snapshot(repository, snapshot, 3)
    assert restored["version"] == exported["version"]
    assert (await repository.report())["enrichments"] == 8
    async with repository.engine.connect() as connection:
        vector = await connection.scalar(select(t.product_embeddings.c.embedding).limit(1))
    assert list(vector) == [1.0, 0.0, 0.0]


async def test_corrupt_feed_rolls_back_catalog(repository, mapping, tmp_path):
    await prepare(repository, mapping)
    previous = await repository.version()
    broken = tmp_path / "broken.xml"
    broken.write_text("<catalog><offer>", encoding="utf-8")
    with pytest.raises(ParseError):
        await import_feed(repository, broken, mapping, "broken")
    assert await repository.version() == previous
    assert (await repository.report())["available_offers"] == 10


async def test_ambiguous_source_offer_ids_produce_diagnostics_not_abort(repository, mapping):
    ambiguous = mapping.model_copy(update={"fields": {**mapping.fields, "offer_id": "categoryId"}})
    report = await import_feed(repository, FIXTURES / "synthetic.xml", ambiguous, "synthetic-only")
    assert report["offers"] > 0
    assert any("multiple values" in row["error"] for row in report["errors"])


async def test_real_feed_sample_imports_shared_article_and_unknown_categories(repository):
    mapping = FeedMapping.model_validate_json(
        (FIXTURES.parents[1] / "catalog_mappings" / "12storeez.json").read_text(encoding="utf-8")
    )
    mapping = mapping.model_copy(update={"active_groups_only": False})
    report = await import_feed(
        repository, FIXTURES / "12storeez-sample.xml", mapping, "verified-raw-sample"
    )
    assert report["products"] == 2 and report["offers"] == 3 and not report["errors"]
    assert set(report["undefined_category_ids"]) == {"100173", "100221"}
    records = [record async for record in repository.iter_products(eligible_only=False)]
    jeans = next(record for record in records if record.product.source_model_id == "22086532")
    assert {offer.sku for offer in jeans.offers} == {"22086532"}
    assert {offer.external_id for offer in jeans.offers} == {"101457", "101458"}
    jacket = next(record for record in records if record.product.source_model_id == "121023")
    assert jacket.offers[0].available and jacket.offers[0].price == 94000
    assert {category.external_id for category in jacket.product.source_categories} >= {
        "100173",
        "100221",
    }


async def test_real_feed_active_group_selection_covers_entire_sample(repository):
    mapping = FeedMapping.model_validate_json(
        (FIXTURES.parents[1] / "catalog_mappings" / "12storeez.json").read_text(encoding="utf-8")
    )
    report = await import_feed(
        repository, FIXTURES / "12storeez-sample.xml", mapping, "verified-raw-sample"
    )
    assert report["selected_active_groups"] == 1
    assert report["skipped_inactive_offers"] == 2
    assert report["offers"] == report["products"] == 1
    assert (await repository.report())["available_offers"] == 1


async def test_truncated_snapshot_is_rolled_back(repository, mapping, tmp_path):
    await prepare(repository, mapping)
    path = tmp_path / "truncated.jsonl"
    await export_snapshot(repository, path, 3)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    async with repository.engine.begin() as connection:
        for table in reversed(t.metadata.sorted_tables):
            await connection.execute(table.delete())
    with pytest.raises(ValueError, match="Incomplete"):
        await restore_snapshot(repository, path, 3)
    assert (await repository.report())["products"] == 0
