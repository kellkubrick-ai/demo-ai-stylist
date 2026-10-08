from unittest.mock import AsyncMock

import pytest
from pydantic_ai.models.test import TestModel

from ai_stylist.config import Settings
from ai_stylist.llm.response import ResponseService
from ai_stylist.pipeline.service import StylingPipeline
from ai_stylist.providers.agents import make_agent
from ai_stylist.providers.prompts import PromptRenderer
from ai_stylist.schemas.intent import StylingIntent
from ai_stylist.schemas.planning import OutfitPlan, StylingUnderstanding
from ai_stylist.schemas.retrieval import CandidatePool, RetrievalCandidate
from ai_stylist.schemas.styling import StylingResponse


def mocks():
    repository = AsyncMock()
    repository.version.return_value = "frozen-test-catalog"
    intent, retriever, stylist, response = (AsyncMock() for _ in range(4))
    for service in (intent, stylist, response):
        service.last_usage = {}
    retriever.last_usage = {}
    renderer = PromptRenderer(Settings().knowledge_dir)
    return repository, intent, retriever, stylist, response, renderer


async def test_clarification_stops_before_retrieval():
    repository, intent, retriever, stylist, response, renderer = mocks()
    intent.understand.return_value = StylingUnderstanding(
        intent=StylingIntent(needs_clarification=True, clarification_question="Какой повод?"),
        outfit_plan=OutfitPlan(),
    )
    result = await StylingPipeline(repository, intent, retriever, stylist, response, renderer).run(
        [{"role": "user", "content": "Хочу образ"}],
    )
    assert result.clarification and not result.failed
    retriever.retrieve.assert_not_awaited()
    stylist.style.assert_not_awaited()
    response.respond.assert_not_awaited()
    assert "error" not in repository.save_run.call_args.args[0]


async def test_empty_pool_is_normal_and_skips_stylist(plan):
    repository, intent, retriever, stylist, response, renderer = mocks()
    intent.understand.return_value = StylingUnderstanding(intent=StylingIntent(), outfit_plan=plan)
    retriever.retrieve.return_value = CandidatePool()
    response.respond.side_effect = lambda request: StylingResponse(
        message="Недостаточно товаров в найденном наборе.",
        looks=request.styling_result.looks,
        limitations=request.styling_result.limitations,
    )
    result = await StylingPipeline(repository, intent, retriever, stylist, response, renderer).run(
        [{"role": "user", "content": "Образ на ужин"}],
    )
    assert not result.failed and not result.cards
    stylist.style.assert_not_awaited()
    log = repository.save_run.call_args.args[0]
    assert log["result_json"]["limitations"] and "error" not in log


@pytest.mark.parametrize("catalog_changed", [False, True])
async def test_full_pipeline_uses_validated_catalog_cards(
    records, plan, styling_result, catalog_changed
):
    repository, intent, retriever, stylist, _, renderer = mocks()
    if catalog_changed:
        repository.version.side_effect = ["frozen-test-catalog", "updated-catalog"]
    intent.understand.return_value = StylingUnderstanding(intent=StylingIntent(), outfit_plan=plan)
    roles = {"blouse": "top", "trousers": "bottom", "shoes": "shoes"}
    selected = [r for r in records if r.product.product_type in roles]
    retriever.retrieve.return_value = CandidatePool(
        products=[
            RetrievalCandidate(
                product=r,
                eligible_slots=[roles[r.product.product_type]],
                retrieval_score=1,
            )
            for r in selected
        ]
    )
    stylist.style.return_value = styling_result
    repository.get_products.return_value = selected
    response_agent = make_agent(
        Settings(),
        "response",
        StylingResponse,
        renderer,
        model=TestModel(
            custom_output_args={"message": "Готово", **styling_result.model_dump(mode="json")},
        ),
    )
    response = ResponseService(response_agent)
    result = await StylingPipeline(repository, intent, retriever, stylist, response, renderer).run(
        [{"role": "user", "content": "Минимализм на ужин"}],
    )
    if catalog_changed:
        assert result.failed and not result.cards
        assert "Catalog version changed" in repository.save_run.call_args.args[0]["error"]
        return
    assert not result.failed
    assert str(result.cards[0].total) == "600.60"
    log = repository.save_run.call_args.args[0]
    assert log["catalog_version"] == "frozen-test-catalog"
    assert log["vlm_request_json"]["products"]
    assert log["result_json"]["looks"]


async def test_provider_failure_is_user_facing_and_recorded(plan):
    repository, intent, retriever, stylist, response, renderer = mocks()
    intent.understand.side_effect = RuntimeError("provider unavailable")
    result = await StylingPipeline(repository, intent, retriever, stylist, response, renderer).run(
        [{"role": "user", "content": "Образ на ужин"}],
    )
    assert result.failed and "Попробуйте" in result.response.message
    assert repository.save_run.call_args.args[0]["error"] == "RuntimeError: provider unavailable"


async def test_invalid_model_output_is_not_sent_to_response(records, plan, styling_result):
    repository, intent, retriever, stylist, response, renderer = mocks()
    intent.understand.return_value = StylingUnderstanding(intent=StylingIntent(), outfit_plan=plan)
    roles = {"blouse": "top", "trousers": "bottom", "shoes": "shoes"}
    selected = [r for r in records if r.product.product_type in roles]
    retriever.retrieve.return_value = CandidatePool(
        products=[
            RetrievalCandidate(
                product=r,
                eligible_slots=[roles[r.product.product_type]],
                retrieval_score=1,
            )
            for r in selected
        ]
    )
    styling_result.looks[0].items[0].offer_id = "invented"
    stylist.style.return_value = styling_result
    repository.get_products.return_value = selected
    result = await StylingPipeline(repository, intent, retriever, stylist, response, renderer).run(
        [{"role": "user", "content": "Образ на ужин"}],
    )
    assert result.failed
    response.respond.assert_not_awaited()
