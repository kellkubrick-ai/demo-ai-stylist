import asyncio
from collections import defaultdict

from aiogram import BaseMiddleware, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


class UserLockMiddleware(BaseMiddleware):
    def __init__(self):
        self.locks = defaultdict(asyncio.Lock)

    async def __call__(self, handler, event, data):
        if event.from_user is None:
            return
        async with self.locks[event.from_user.id]:
            return await handler(event, data)


def keyboard(active=False):
    labels = ["Завершить подбор", "Сбросить подбор"] if active else ["AI-стилист"]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in labels]],
        resize_keyboard=True,
    )


async def show_result(message, result):
    text = result.response.message
    if result.response.limitations:
        text += "\n\n" + "\n".join(result.response.limitations)
    if result.response.follow_up_question:
        text += "\n\n" + result.response.follow_up_question
    for offset in range(0, len(text), 4000):
        await message.answer(text[offset : offset + 4000], reply_markup=keyboard(True))
    for number, (look, cards) in enumerate(
        zip(result.response.looks, result.cards, strict=True), 1
    ):
        await message.answer(
            f"Образ {number}: {look.explanation}\nИтого: {cards.total:.2f} {cards.currency}"
        )
        for card in cards.items:
            caption = f"{card.title}\n{card.price:.2f} {card.currency}"
            if card.size:
                caption += f"\nРазмер предложения: {card.size}"
            if card.sku:
                caption += f"\nАртикул: {card.sku}"
            if card.product_url:
                caption += "\n" + card.product_url
            if card.image_url:
                try:
                    await message.answer_photo(card.image_url, caption=caption[:1024])
                    continue
                except TelegramBadRequest:
                    pass
            await message.answer(caption[:4000])


def make_router(pipeline):
    router = Router()
    router.message.outer_middleware(UserLockMiddleware())
    repository = pipeline.repository

    @router.message(Command("start"))
    async def start(message):
        session = await repository.find_session(message.from_user.id)
        if session:
            await repository.update_session(session["id"], status="finished")
        await message.answer("Нажмите AI-стилист, чтобы подобрать образ.", reply_markup=keyboard())

    @router.message(Command("stylist"))
    async def enter(message):
        await repository.active_session(message.from_user.id)
        await message.answer(
            "Расскажите о поводе, стиле и ограничениях.", reply_markup=keyboard(True)
        )

    @router.message(Command("finish"))
    async def finish(message):
        session = await repository.find_session(message.from_user.id)
        if session:
            await repository.update_session(session["id"], status="finished")
        await message.answer("Подбор завершён.", reply_markup=keyboard())

    @router.message(Command("reset"))
    async def reset(message):
        session = await repository.find_session(message.from_user.id)
        if session:
            await repository.update_session(session["id"], status="finished")
        await repository.active_session(message.from_user.id)
        await message.answer("Начнём новый подбор. Что вам нужно?", reply_markup=keyboard(True))

    @router.message()
    async def text(message):
        if not message.text:
            await message.answer("Для подбора отправьте текстовое описание.")
            return
        if message.text == "AI-стилист" or message.text.casefold() == "стилист":
            await enter(message)
            return
        if message.text == "Завершить подбор":
            await finish(message)
            return
        if message.text == "Сбросить подбор":
            await reset(message)
            return
        session = await repository.find_session(message.from_user.id)
        if not session:
            await message.answer(
                "Включите AI-стилиста для подбора образов.", reply_markup=keyboard()
            )
            return
        history = [*session["dialogue_history"], {"role": "user", "content": message.text}]
        await repository.update_session(session["id"], dialogue_history=history)
        result = await pipeline.run(history, session_id=session["id"])
        history = [*history, {"role": "assistant", "content": result.response.message}]
        await repository.update_session(session["id"], dialogue_history=history)
        await show_result(message, result)

    return router
