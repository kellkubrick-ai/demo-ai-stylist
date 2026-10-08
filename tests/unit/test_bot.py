from types import SimpleNamespace
from unittest.mock import AsyncMock

from ai_stylist.bot.handlers import make_router, show_result
from ai_stylist.pipeline.service import PipelineResult, catalog_cards
from ai_stylist.schemas.styling import StylingResponse


def message(text):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
        answer_photo=AsyncMock(),
    )


async def test_text_outside_stylist_mode_does_not_run_pipeline():
    pipeline = AsyncMock()
    pipeline.repository.find_session.return_value = None
    event = message("Нужен образ")
    await make_router(pipeline).message.handlers[-1].callback(event)
    pipeline.run.assert_not_awaited()
    assert "Включите" in event.answer.call_args.args[0]


async def test_reset_finishes_old_session_and_enters_new_one():
    pipeline = AsyncMock()
    pipeline.repository.find_session.return_value = {"id": "old"}
    event = message("Сбросить подбор")
    await make_router(pipeline).message.handlers[-1].callback(event)
    pipeline.repository.update_session.assert_awaited_once_with("old", status="finished")
    pipeline.repository.active_session.assert_awaited_once_with(42)


async def test_clarification_is_kept_in_same_dialogue():
    pipeline = AsyncMock()
    pipeline.repository.find_session.return_value = {"id": "session", "dialogue_history": []}
    pipeline.run.return_value = PipelineResult(
        run_id="run",
        clarification=True,
        response=StylingResponse(message="Какой размер обуви?"),
    )
    event = message("Нужен образ на ужин")
    await make_router(pipeline).message.handlers[-1].callback(event)
    pipeline.run.assert_awaited_once_with(
        [{"role": "user", "content": "Нужен образ на ужин"}],
        session_id="session",
    )
    history = pipeline.repository.update_session.call_args.kwargs["dialogue_history"]
    assert history[-1] == {"role": "assistant", "content": "Какой размер обуви?"}


async def test_catalog_cards_include_actual_links_prices_and_photos(records, styling_result):
    result = PipelineResult(
        run_id="run",
        response=StylingResponse(
            message="Готово",
            looks=styling_result.looks,
            limitations=styling_result.limitations,
        ),
        cards=catalog_cards(styling_result.looks, records),
    )
    event = message("")
    await show_result(event, result)
    assert event.answer_photo.await_count == 3
    caption = event.answer_photo.call_args_list[0].kwargs["caption"]
    assert "100.10 RUB" in caption and "https://shop.example.org/top-black" in caption
