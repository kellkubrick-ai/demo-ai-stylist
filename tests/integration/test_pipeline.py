from pathlib import Path

import httpx
import pytest
from pydantic_ai.models.test import TestModel

from ai_stylist.catalog.importer import import_feed
from ai_stylist.config import Settings
from ai_stylist.llm.intent import IntentService
from ai_stylist.llm.response import ResponseService
from ai_stylist.pipeline.service import StylingPipeline
from ai_stylist.providers.agents import make_agent
from ai_stylist.providers.embeddings import EmbeddingClient
from ai_stylist.providers.images import ImageLoader
from ai_stylist.providers.prompts import PromptRenderer
from ai_stylist.retriever.service import Retriever
from ai_stylist.schemas.intent import StylingIntent
from ai_stylist.schemas.planning import StylingUnderstanding
from ai_stylist.schemas.styling import StylingResponse, VLMStylingResponse
from ai_stylist.stylist.service import StylistService

pytestmark = pytest.mark.integration


async def test_two_complete_looks_end_to_end(repository, mapping, records, plan, look_factory):
    from io import BytesIO

    from PIL import Image

    fixture = Path(__file__).parents[1] / "fixtures" / "synthetic.xml"
    await import_feed(repository, fixture, mapping, "synthetic-only")
    for record in records:
        for url in record.product.image_urls:
            await repository.mark_image(record.product.id, str(url), True)
        await repository.save_embedding(record.product.id, [1, 0, 0], "test/embedding")
    first = look_factory(records)
    second = first.model_copy(deep=True)
    beige = next(r for r in records if r.product.color == "Бежевый")
    second.items[0].product_id = beige.product.id
    second.items[0].offer_id = beige.offers[0].id
    styled = VLMStylingResponse(looks=[first, second])
    settings = Settings()
    renderer = PromptRenderer(settings.knowledge_dir)
    intent_agent = make_agent(
        settings,
        "intent",
        StylingUnderstanding,
        renderer,
        model=TestModel(
            custom_output_args=StylingUnderstanding(
                intent=StylingIntent(
                    sizes_by_role={"top": ["M"], "bottom": ["M"], "shoes": ["38"]}
                ),
                outfit_plan=plan,
            ).model_dump(mode="json"),
        ),
    )
    stylist_agent = make_agent(
        settings,
        "stylist",
        VLMStylingResponse,
        renderer,
        model=TestModel(custom_output_args=styled.model_dump(mode="json")),
    )
    response_agent = make_agent(
        settings,
        "response",
        StylingResponse,
        renderer,
        model=TestModel(
            custom_output_args={
                "message": "Два образа из реальных товаров.",
                **styled.model_dump(mode="json"),
            },
        ),
    )
    image_requests = []

    def transport(request):
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "data": [{"index": 0, "embedding": [1, 0, 0]}],
                    "usage": {"total_tokens": 3, "cost": 0.001},
                },
            )
        image_requests.append(str(request.url))
        data = BytesIO()
        color = "beige" if "beige" in str(request.url) else "black"
        Image.new("RGB", (2, 2), color).save(data, format="PNG")
        return httpx.Response(200, content=data.getvalue())

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        pipeline = StylingPipeline(
            repository,
            IntentService(intent_agent, renderer, repository),
            Retriever(repository, EmbeddingClient(client, "fake-test-key", "test/embedding", 3)),
            StylistService(stylist_agent, ImageLoader(client, 10000, repository)),
            ResponseService(response_agent),
            renderer,
        )
        result = await pipeline.run(
            [{"role": "user", "content": "Образ на ужин, размеры M и обувь 38."}]
        )
    assert not result.failed
    assert len(result.response.looks) == 2 and len(result.cards) == 2
    assert str(result.cards[0].total) == "600.60"
    assert str(result.cards[1].total) == "620.50"
    assert image_requests
    logged = await repository.get_run(result.run_id)
    assert logged["error"] is None
    assert logged["metrics"]["intent"]["requests"] == 1
    assert logged["metrics"]["stylist"]["requests"] == 1
    assert logged["metrics"]["response"]["requests"] == 1
    assert logged["metrics"]["retrieval"]["requests"] == 4
