import io
from unittest.mock import AsyncMock

import httpx
import pytest
from jinja2 import UndefinedError
from PIL import Image, UnidentifiedImageError
from pydantic_ai import BinaryContent
from pydantic_ai.models.test import TestModel

from ai_stylist.catalog.enrichment.service import EnrichmentService
from ai_stylist.config import Settings
from ai_stylist.providers.agents import make_agent
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


async def test_broken_primary_photo_falls_back_to_two_valid_variant_photos():
    repository = AsyncMock()

    def transport(request):
        if request.url.path == "/broken":
            return httpx.Response(404)
        return httpx.Response(200, content=png_bytes())

    urls = [f"https://image.example/{name}" for name in ("broken", "second", "third", "fourth")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        selected, content = await ImageLoader(client, 10000, repository).select("p", urls)
    assert selected == urls[1:3]
    assert len(content) == 4 and all(isinstance(content[i], BinaryContent) for i in (1, 3))
    assert repository.mark_image.await_count == 3
    repository.mark_image.assert_any_await("p", urls[0], False)


async def test_no_valid_photos_prevents_enrichment():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"not a photo"))
    ) as client:
        with pytest.raises(ValueError, match="No usable"):
            await ImageLoader(client, 10000).select("p", ["https://image.example/broken"])


def test_openrouter_model_configuration_rejects_framework_prefix():
    with pytest.raises(ValueError, match="provider/model"):
        Settings(intent_model="openrouter:provider/model").require("intent_model")
    Settings(intent_model="provider/model").require("intent_model")


async def test_enrichment_is_structured_and_reused(records):
    renderer = PromptRenderer(Settings().knowledge_dir)
    model = TestModel(custom_output_args={"styles": ["minimal"], "fit": "relaxed"})
    agent = make_agent(Settings(), "enrichment", ProductEnrichment, renderer, model=model)
    images = AsyncMock()
    images.select.return_value = (
        [str(records[0].product.image_urls[0])],
        [BinaryContent(data=png_bytes(), media_type="image/png")],
    )
    repository = AsyncMock()
    service = EnrichmentService(agent, renderer, images, repository, "test-model")
    record = records[0]
    assert await service.enrich(record) == "enriched"
    assert model.last_model_request_parameters.output_tools
    output = repository.save_enrichment.call_args.args[1]
    assert output.styles == ["minimal"]
    record.enrichment = output
    assert await service.enrich(record) == "reused"
    assert repository.save_enrichment.await_count == 1
    assert images.select.await_count == 1


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
