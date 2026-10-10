import json
from unittest.mock import AsyncMock

from ai_stylist.catalog.embeddings import rebuild_embeddings
from ai_stylist.catalog.enrichment.service import append_trace, prepare_catalog


class ProductRepository:
    def __init__(self, records):
        self.records = records
        self.has_embedding = AsyncMock(return_value=False)
        self.save_embedding = AsyncMock()

    async def iter_products(self):
        for record in self.records:
            yield record


async def test_embeddings_process_only_selected_products(records, tmp_path):
    repository = ProductRepository(records)
    embedder = type("Embedder", (), {"model": "test", "embed": AsyncMock(return_value=[1.0])})()
    product_id = records[2].product.id

    counts = await rebuild_embeddings(
        repository, embedder, tmp_path / "embeddings.jsonl", product_ids={product_id}
    )

    assert counts == {"embedded": 1, "reused": 0, "failed": 0}
    embedder.embed.assert_awaited_once()
    assert repository.save_embedding.await_args.args[0] == product_id


async def test_enrichment_processes_only_selected_products(records, tmp_path):
    repository = ProductRepository(records)
    service = type(
        "Service",
        (),
        {
            "enrich": AsyncMock(
                return_value={
                    "status": "enriched",
                    "model_id": "test/model",
                    "input": {"product": {"title": "Тест"}},
                    "output": {"styles": ["минимализм"]},
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "error": None,
                }
            )
        },
    )()
    product_id = records[2].product.id

    counts = await prepare_catalog(
        repository, service, tmp_path / "enrich.jsonl", product_ids={product_id}
    )

    assert counts == {"enriched": 1, "reused": 0, "failed": 0}
    assert service.enrich.await_args.args[0].product.id == product_id
    service.enrich.assert_awaited_once()
    row = json.loads((tmp_path / "enrich.jsonl").read_text(encoding="utf-8"))
    assert row["input"]["product"]["title"] == "Тест"
    assert row["output"]["styles"] == ["минимализм"]


def test_enrichment_trace_can_be_written_as_markdown(tmp_path):
    report_path = tmp_path / "trace.md"
    append_trace(
        report_path,
        {
            "product_id": "product-1",
            "status": "enriched",
            "model_id": "google/test-model",
            "input": {
                "instructions": "Опиши вещь на русском языке.",
                "reasoning_effort": "low",
                "product": {"title": "Бирюзовое платье"},
                "images": [{"url": "https://example.com/dress.jpg"}],
            },
            "output": {"styles": ["минимализм"]},
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "error": None,
        },
    )
    markdown = report_path.read_text(encoding="utf-8")

    assert markdown.startswith("# Лог VLM-обогащения каталога")
    assert "## Бирюзовое платье" in markdown
    assert "### Вход модели" in markdown
    assert "![Фото 1](https://example.com/dress.jpg)" in markdown
    assert '"минимализм"' in markdown
