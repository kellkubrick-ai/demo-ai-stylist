import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import BufferedInputFile
from PIL import Image

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
    event = message("Новый подбор")
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
    pipeline.run.assert_awaited_once()
    assert pipeline.run.call_args.args == (
        [{"role": "user", "content": "Нужен образ на ужин"}],
    )
    assert pipeline.run.call_args.kwargs["session_id"] == "session"
    assert callable(pipeline.run.call_args.kwargs["on_search_start"])
    event.answer.assert_awaited_once()
    assert "Какой размер обуви?" in event.answer.call_args.args[0]
    history = pipeline.repository.update_session.call_args.kwargs["dialogue_history"]
    assert history[-1] == {"role": "assistant", "content": "Какой размер обуви?"}


async def test_search_status_is_sent_when_pipeline_starts_search():
    pipeline = AsyncMock()
    pipeline.repository.find_session.return_value = {"id": "session", "dialogue_history": []}

    async def run(_, *, on_search_start, **kwargs):
        await on_search_start()
        return PipelineResult(run_id="run", response=StylingResponse(message="Готово"))

    pipeline.run.side_effect = run
    event = message("Образ на ужин")
    await make_router(pipeline).message.handlers[-1].callback(event)
    assert event.answer.await_count == 2
    assert "🔎 Ищу подходящие товары" in event.answer.call_args_list[0].args[0]
    assert event.answer.call_args_list[1].args[0] == "Готово"


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
    photo = io.BytesIO()
    Image.new("RGB", (30, 40), "red").save(photo, format="PNG")
    images = SimpleNamespace(load=AsyncMock(return_value=SimpleNamespace(data=photo.getvalue())))
    await show_result(event, result, images)
    event.answer.assert_not_awaited()
    event.answer_photo.assert_awaited_once()
    sent = event.answer_photo.call_args
    assert isinstance(sent.args[0], BufferedInputFile)
    with Image.open(io.BytesIO(sent.args[0].data)) as collage:
        assert collage.size == (956, 332)
    caption = sent.kwargs["caption"]
    assert "Образ 1" in caption and "100,10 ₽" in caption
    assert '<a href="https://shop.example.org/top-black">ссылка</a>' in caption
    assert images.load.await_count == 3


async def test_multiple_looks_still_send_one_message(records, styling_result):
    look = styling_result.looks[0]
    cards = catalog_cards([look], records)[0]
    result = PipelineResult(
        run_id="run",
        response=StylingResponse(message="Готово", looks=[look, look]),
        cards=[cards, cards],
    )
    photo = io.BytesIO()
    Image.new("RGB", (30, 40), "red").save(photo, format="PNG")
    images = SimpleNamespace(load=AsyncMock(return_value=SimpleNamespace(data=photo.getvalue())))
    event = message("")
    await show_result(event, result, images)
    event.answer.assert_not_awaited()
    event.answer_photo.assert_awaited_once()
    assert "Образ 1" in event.answer_photo.call_args.kwargs["caption"]
    assert "Образ 2" in event.answer_photo.call_args.kwargs["caption"]
    assert images.load.await_count == 3


async def test_long_explanation_does_not_split_the_photo_reply(records, styling_result):
    styling_result.looks[0].explanation = "Подробное объяснение. " * 100
    result = PipelineResult(
        run_id="run",
        response=StylingResponse(message="Готово", looks=styling_result.looks),
        cards=catalog_cards(styling_result.looks, records),
    )
    photo = io.BytesIO()
    Image.new("RGB", (30, 40), "red").save(photo, format="PNG")
    images = SimpleNamespace(load=AsyncMock(return_value=SimpleNamespace(data=photo.getvalue())))
    event = message("")
    await show_result(event, result, images)
    event.answer.assert_not_awaited()
    event.answer_photo.assert_awaited_once()
    caption = event.answer_photo.call_args.kwargs["caption"]
    assert "Подробное объяснение" not in caption
    assert "Образ 1" in caption and "ссылка" in caption


async def test_empty_result_sends_one_text_message():
    result = PipelineResult(
        run_id="run",
        response=StylingResponse(
            message="Нет подходящих товаров", limitations=["Не нашлась обувь."]
        ),
    )
    event = message("")
    images = SimpleNamespace(load=AsyncMock())
    await show_result(event, result, images)
    event.answer.assert_awaited_once()
    event.answer_photo.assert_not_awaited()
    images.load.assert_not_awaited()
    assert "Не нашлась обувь" in event.answer.call_args.args[0]
