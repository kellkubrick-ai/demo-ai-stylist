import io
from unittest.mock import AsyncMock

import httpx
import pytest
from jinja2 import UndefinedError
from PIL import Image, UnidentifiedImageError
from pydantic_ai import BinaryContent, ImageUrl
from pydantic_ai.models.test import TestModel

from ai_stylist.catalog.enrichment.service import EnrichmentService
from ai_stylist.config import Settings
from ai_stylist.providers.agents import (
    enrichment_contract_issues,
    enrichment_latin_fields,
    make_agent,
    stage_model_settings,
)
from ai_stylist.providers.embeddings import EmbeddingClient
from ai_stylist.providers.images import ImageLoader
from ai_stylist.providers.prompts import PromptRenderer
from ai_stylist.schemas.enrichment import EnrichmentRequest, ProductEnrichment


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "black").save(output, format="PNG")
    return output.getvalue()


async def test_photos_are_binary_attachments_with_ids():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=png_bytes()))
    ) as client:
        repository = AsyncMock()
        loader = ImageLoader(client, 10000, repository)
        content = await loader.attach(
            [
                EnrichmentRequest(
                    product_id="exact-black-variant",
                    title="Top",
                    image_urls=["https://image.example/x.png"],
                )
            ]
        )
        assert "exact-black-variant" in content[0]
        assert isinstance(content[1], BinaryContent)
        assert content[1].media_type == "image/png"
        repository.mark_image.assert_awaited_once_with(
            "exact-black-variant", "https://image.example/x.png", True
        )


@pytest.mark.parametrize("body,max_bytes", [(b"not an image", 1000), (png_bytes(), 3)])
async def test_bad_or_oversized_images_are_not_marked_usable(body, max_bytes):
    repository = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as client:
        loader = ImageLoader(client, max_bytes, repository)
        with pytest.raises((ValueError, UnidentifiedImageError)):
            await loader.load("p", "https://image.example/x")
    repository.mark_image.assert_awaited_once_with("p", "https://image.example/x", False)


async def test_enrichment_uses_two_remote_variant_photo_urls_without_downloading():
    repository = AsyncMock()

    def transport(request):
        raise AssertionError(f"Unexpected local image download: {request.url}")

    urls = [f"https://image.example/{name}.jpg" for name in ("first", "second", "third")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        selected, content = await ImageLoader(client, 10000, repository).select("p", urls)
    assert selected == urls[:2]
    assert len(content) == 4 and all(isinstance(content[i], ImageUrl) for i in (1, 3))
    assert [content[i].url for i in (1, 3)] == urls[:2]
    repository.mark_image.assert_not_awaited()


async def test_no_valid_photos_prevents_enrichment():
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="No public"):
            await ImageLoader(client, 10000).select("p", ["not-a-public-url"])


def test_openrouter_model_configuration_rejects_framework_prefix():
    with pytest.raises(ValueError, match="provider/model"):
        Settings(intent_model="openrouter:provider/model").require("intent_model")
    Settings(intent_model="provider/model").require("intent_model")


def test_reasoning_effort_is_configured_per_stage():
    settings = Settings()
    expected = {
        "intent": "medium",
        "enrichment": "low",
        "stylist": "high",
        "response": "none",
    }
    for stage, effort in expected.items():
        assert stage_model_settings(settings, stage) == {
            "openrouter_usage": {"include": True},
            "openrouter_reasoning": {"effort": effort, "exclude": True},
        }


async def test_enrichment_is_structured_and_reused(records):
    renderer = PromptRenderer(Settings().knowledge_dir)
    model = TestModel(custom_output_args={"styles": ["минимализм"], "fit": "свободная"})
    agent = make_agent(Settings(), "enrichment", ProductEnrichment, renderer, model=model)
    images = AsyncMock()
    images.select.return_value = (
        [str(records[0].product.image_urls[0])],
        [ImageUrl(str(records[0].product.image_urls[0]))],
    )
    repository = AsyncMock()
    service = EnrichmentService(agent, renderer, images, repository, "test-model")
    record = records[0]
    run = await service.enrich(record)
    assert run["status"] == "enriched"
    assert run["input"]["product"]["product_id"] == record.product.id
    assert run["input"]["images"][0]["url"] == str(record.product.image_urls[0])
    assert run["output"]["styles"] == ["минимализм"]
    assert model.last_model_request_parameters.output_tools
    output = repository.save_enrichment.call_args.args[1]
    assert output.styles == ["минимализм"]
    record.enrichment = output
    assert (await service.enrich(record))["status"] == "reused"
    assert repository.save_enrichment.await_count == 1
    repository.mark_image.assert_awaited_once_with(
        record.product.id, str(record.product.image_urls[0]), True
    )
    assert images.select.await_count == 1


def test_enrichment_requires_russian_text_values():
    valid = ProductEnrichment(
        styles=["минимализм"],
        silhouette="прямой",
        enriched_description="Лаконичный жакет свободного силуэта.",
    )
    mixed = valid.model_copy(update={"styles": ["minimalist"], "fit": "relaxed"})
    assert enrichment_latin_fields(valid) == []
    assert enrichment_latin_fields(mixed) == ["styles", "fit"]


def test_enrichment_contract_limits_categorical_values():
    valid = ProductEnrichment(
        styles=["минимализм", "современная элегантность", "вечерний стиль"],
        visual_weight="средний",
    )
    invalid = valid.model_copy(
        update={
            "styles": ["один", "два", "три", "четыре", "пять"],
            "visual_weight": "умеренно лёгкий",
        }
    )

    assert enrichment_contract_issues(valid) == []
    assert enrichment_contract_issues(invalid) == [
        "styles must contain at most 4 values",
        "visual_weight must be лёгкий, средний, тяжёлый, or null",
    ]


def test_enrichment_prompt_requires_russian_values():
    renderer = PromptRenderer(Settings().knowledge_dir)
    prompt = renderer.render("enrichment", renderer.knowledge())
    assert "every textual value in Russian using Cyrillic" in prompt


def test_enrichment_prompt_separates_observation_and_interpretation():
    renderer = PromptRenderer(Settings().knowledge_dir)
    prompt = renderer.render("enrichment", renderer.knowledge())

    assert "Observation fields must be grounded in the photos" in prompt
    assert "Catalog categories may inform occasions" in prompt
    assert "Do not call overlapping panels a wrap" in prompt
    assert "visual layering role and compatibility only" in prompt
    assert "Stylist knowledge" not in prompt
    assert "budget" not in prompt
    assert "accessories" not in prompt


def test_prompt_variables_are_explicit():
    with pytest.raises(UndefinedError):
        PromptRenderer(Settings().knowledge_dir).render("stylist", {})


@pytest.mark.parametrize("vector", [[1.0, 2.0], [0.0, 0.0, 0.0], [1.0, float("inf"), 3.0]])
async def test_embedding_validation(vector):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": vector}], "usage": {"total_tokens": 1}},
            )
        )
    ) as client:
        with pytest.raises(ValueError):
            await EmbeddingClient(client, "test", "test/model", 3).embed("product")
