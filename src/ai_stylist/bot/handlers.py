import asyncio
import io
import logging
from collections import defaultdict
from html import escape

from aiogram import BaseMiddleware, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, KeyboardButton, ReplyKeyboardMarkup
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)


class UserLockMiddleware(BaseMiddleware):
    def __init__(self):
        self.locks = defaultdict(asyncio.Lock)

    async def __call__(self, handler, event, data):
        if event.from_user is None:
            return
        async with self.locks[event.from_user.id]:
            return await handler(event, data)


def keyboard(active=False):
    labels = ["Новый подбор"] if active else ["AI-стилист"]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in labels]],
        resize_keyboard=True,
    )


def format_price(amount, currency):
    value = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    if value.endswith(",00"):
        value = value[:-3]
    return f"{value} {'₽' if currency == 'RUB' else currency}"


def result_text(result, *, include_intro=True, include_explanations=True):
    plain, html, visible = [], [], []

    def add(value, formatted=None, caption_value=None):
        plain.append(value)
        html.append(formatted if formatted is not None else escape(value))
        visible.append(caption_value if caption_value is not None else value)

    if include_intro and result.response.message.strip():
        add(result.response.message.strip())
    for number, (look, cards) in enumerate(
        zip(result.response.looks, result.cards, strict=True), 1
    ):
        if plain:
            add("")
        heading = f"Образ {number} · Итого {format_price(cards.total, cards.currency)}"
        add(heading, f"<b>{escape(heading)}</b>")
        if include_explanations and look.explanation.strip():
            add(look.explanation.strip())
        for item_number, card in enumerate(cards.items, 1):
            label = (
                f"{number}.{item_number} {card.title} · "
                f"{format_price(card.price, card.currency)}"
            )
            if card.product_url:
                add(
                    f"{label} · {card.product_url}",
                    f'{escape(label)} · '
                    f'<a href="{escape(card.product_url, quote=True)}">ссылка</a>',
                    f"{label} · ссылка",
                )
            else:
                add(label)
    if len(result.response.looks) < 2:
        for limitation in result.response.limitations:
            if limitation.strip():
                if plain:
                    add("")
                add(limitation.strip())
    if result.response.follow_up_question:
        if plain:
            add("")
        add(result.response.follow_up_question)
    return "\n".join(plain), "\n".join(html), "\n".join(visible)


async def make_collage(cards, images):
    urls = {
        card.image_url: card
        for look in cards
        for card in look.items
        if card.image_url
    }

    async def load(url, card):
        try:
            return (await images.load(card.product_id, url)).data
        except Exception:
            logger.warning("Could not load product photo %s for Telegram collage", card.product_id)
            return None

    downloaded = await asyncio.gather(*(load(url, card) for url, card in urls.items()))
    payloads = dict(zip(urls, downloaded, strict=True))
    if not any(payloads.values()):
        return None

    tile_size, gap, margin = 300, 12, 16
    columns = min(3, max(len(look.items) for look in cards))
    rows = []
    for look_number, look in enumerate(cards, 1):
        numbered = list(enumerate(look.items, 1))
        rows.extend(
            (look_number, numbered[start : start + columns])
            for start in range(0, len(numbered), columns)
        )
    width = margin * 2 + columns * tile_size + (columns - 1) * gap
    height = margin * 2 + len(rows) * tile_size + (len(rows) - 1) * gap
    height += sum(rows[index][0] != rows[index - 1][0] for index in range(1, len(rows))) * gap
    collage = Image.new("RGB", (width, height), "#f3f3f3")
    draw = ImageDraw.Draw(collage)
    font = ImageFont.load_default(size=24)
    decoded_count = 0
    y = margin
    for row_index, (look_number, row) in enumerate(rows):
        if row_index and look_number != rows[row_index - 1][0]:
            y += gap
        for column, (item_number, card) in enumerate(row):
            x = margin + column * (tile_size + gap)
            draw.rectangle((x, y, x + tile_size - 1, y + tile_size - 1), fill="white")
            payload = payloads.get(card.image_url)
            if payload:
                try:
                    with Image.open(io.BytesIO(payload)) as source:
                        photo = ImageOps.exif_transpose(source)
                        photo.thumbnail((tile_size - 24, tile_size - 24), Image.Resampling.LANCZOS)
                        photo = photo.convert("RGBA")
                    collage.paste(
                        photo,
                        (x + (tile_size - photo.width) // 2, y + (tile_size - photo.height) // 2),
                        photo,
                    )
                    decoded_count += 1
                except OSError:
                    logger.warning(
                        "Could not decode product photo %s for Telegram collage", card.product_id
                    )
            badge = f"{look_number}.{item_number}"
            draw.rounded_rectangle((x + 8, y + 8, x + 68, y + 42), radius=8, fill="#222222")
            draw.text((x + 16, y + 12), badge, font=font, fill="white")
        y += tile_size + gap
    if not decoded_count:
        return None
    output = io.BytesIO()
    collage.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()


async def show_result(message, result, images):
    plain, caption, visible = result_text(result)
    if not result.cards:
        await message.answer(plain, reply_markup=keyboard(True))
        return
    if len(visible) > 1024:
        plain, caption, visible = result_text(result, include_explanations=False)
    if len(visible) > 1024:
        plain, caption, visible = result_text(
            result, include_intro=False, include_explanations=False
        )
    if len(visible) <= 1024:
        photo = await make_collage(result.cards, images)
        if photo:
            try:
                await message.answer_photo(
                    BufferedInputFile(photo, filename=f"looks-{result.run_id}.jpg"),
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard(True),
                )
                return
            except TelegramBadRequest:
                logger.warning("Telegram rejected the look collage for run %s", result.run_id)
        first_url = next(
            (card.image_url for look in result.cards for card in look.items if card.image_url),
            None,
        )
        if first_url:
            try:
                await message.answer_photo(
                    first_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard(True),
                )
                return
            except TelegramBadRequest:
                logger.warning(
                    "Telegram rejected the fallback product photo for run %s", result.run_id
                )
    _, html, _ = result_text(result, include_explanations=False)
    await message.answer(html, parse_mode=ParseMode.HTML, reply_markup=keyboard(True))


def make_router(pipeline):
    router = Router()
    router.message.outer_middleware(UserLockMiddleware())
    repository = pipeline.repository

    @router.message(Command("start"))
    async def start(message):
        session = await repository.find_session(message.from_user.id)
        if session:
            await repository.update_session(session["id"], status="finished")
        await message.answer(
            "Я AI-стилист по каталогу 12 STOREEZ. Помогу подобрать вещи и образы. "
            "Нажмите «AI-стилист», чтобы начать.",
            reply_markup=keyboard(),
        )

    @router.message(Command("stylist"))
    async def enter(message):
        await repository.active_session(message.from_user.id)
        await message.answer(
            "Подбираю вещи из каталога 12 STOREEZ. "
            "Расскажите о поводе, стиле и ограничениях.",
            reply_markup=keyboard(True),
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
        if message.text in {"Новый подбор", "Сбросить подбор"}:
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

        async def on_search_start():
            try:
                await message.answer(
                    "🔎 Ищу подходящие товары и составляю образ — это может занять около минуты."
                )
            except TelegramAPIError:
                logger.warning("Could not send search status to Telegram")

        result = await pipeline.run(
            history, session_id=session["id"], on_search_start=on_search_start
        )
        reply, _, _ = result_text(result, include_explanations=False)
        history = [*history, {"role": "assistant", "content": reply}]
        await repository.update_session(session["id"], dialogue_history=history)
        await show_result(message, result, pipeline.stylist.images)

    return router
