import time
from decimal import Decimal
from uuid import uuid4

from pydantic import Field

from ai_stylist.retriever.service import covered_plan
from ai_stylist.schemas.base import Schema
from ai_stylist.schemas.retrieval import RetrievalRequest
from ai_stylist.schemas.styling import ResponseRequest, StylingResponse, VLMStylingResponse
from ai_stylist.stylist.service import build_styling_request
from ai_stylist.stylist.validation import validate_styling


class ProductCard(Schema):
    slot: str
    product_id: str
    offer_id: str
    title: str
    product_url: str | None
    image_url: str | None
    size: str | None
    sku: str | None
    price: Decimal
    currency: str


class LookCards(Schema):
    items: list[ProductCard]
    total: Decimal
    currency: str


class PipelineResult(Schema):
    run_id: str
    response: StylingResponse
    cards: list[LookCards] = Field(default_factory=list)
    clarification: bool = False
    failed: bool = False


def catalog_cards(looks, catalog):
    records = {record.product.id: record for record in catalog}
    cards = []
    for look in looks:
        items = []
        for item in look.items:
            record = records[item.product_id]
            offer = next(o for o in record.offers if o.id == item.offer_id)
            items.append(
                ProductCard(
                    slot=item.slot,
                    product_id=item.product_id,
                    offer_id=item.offer_id,
                    title=record.product.title,
                    product_url=str(record.product.product_url)
                    if record.product.product_url
                    else None,
                    image_url=str(record.product.image_urls[0])
                    if record.product.image_urls
                    else None,
                    size=offer.size,
                    sku=offer.sku,
                    price=offer.price,
                    currency=offer.currency,
                )
            )
        cards.append(
            LookCards(
                items=items,
                total=sum((i.price for i in items), Decimal("0")),
                currency=items[0].currency,
            )
        )
    return cards


class StylingPipeline:
    def __init__(self, repository, intent, retriever, stylist, response, renderer, max_products=30):
        self.repository = repository
        self.intent = intent
        self.retriever = retriever
        self.stylist = stylist
        self.response = response
        self.renderer = renderer
        self.max_products = max_products

    async def run(self, dialogue: list[dict], *, session_id=None) -> PipelineResult:
        run_id = str(uuid4())
        started = time.monotonic()
        log = {
            "id": run_id,
            "session_id": session_id,
            "user_request": dialogue[-1]["content"],
            "catalog_version": await self.repository.version(),
            "intent_json": {},
            "outfit_plan_json": {},
            "metrics": {},
        }
        try:
            understanding = await self.intent.understand(dialogue)
            intent, plan = understanding.intent, understanding.outfit_plan
            log.update(
                intent_json=intent.model_dump(mode="json"),
                outfit_plan_json=plan.model_dump(mode="json"),
            )
            log["metrics"]["intent"] = self.intent.last_usage
            if session_id:
                await self.repository.update_session(
                    session_id,
                    current_intent=log["intent_json"],
                    current_outfit_plan=log["outfit_plan_json"],
                )
            if intent.needs_clarification:
                result = PipelineResult(
                    run_id=run_id,
                    clarification=True,
                    response=StylingResponse(message=intent.clarification_question),
                )
            else:
                pool = await self.retriever.retrieve(
                    RetrievalRequest(
                        intent=intent,
                        outfit_plan=plan,
                        max_products=self.max_products,
                    )
                )
                log["metrics"]["retrieval"] = self.retriever.last_usage
                log["metrics"]["candidate_pool"] = pool.model_dump(mode="json")
                viable = covered_plan(pool, plan, intent)
                if not viable.compositions:
                    roles = ", ".join(f"{m.plan_id}: {m.slot}" for m in pool.missing_required_slots)
                    limitation = "В найденном наборе не хватает товаров для полного образа"
                    if roles:
                        limitation += f" ({roles})"
                    styled = VLMStylingResponse(limitations=[limitation + "."])
                    catalog = []
                else:
                    request = build_styling_request(intent, plan, pool, self.renderer.knowledge())
                    log["vlm_request_json"] = request.model_dump(mode="json")
                    styled = await self.stylist.style(request)
                    log["metrics"]["stylist"] = self.stylist.last_usage
                    catalog = await self.repository.get_products(
                        [p.product_id for p in request.products],
                        usable_images_only=True,
                    )
                    validate_styling(request, styled, catalog)
                log["result_json"] = styled.model_dump(mode="json")
                rewritten = await self.response.respond(
                    ResponseRequest(
                        intent=intent,
                        styling_result=styled,
                    )
                )
                log["metrics"]["response"] = self.response.last_usage
                if await self.repository.version() != log["catalog_version"]:
                    raise ValueError("Catalog version changed during styling; rerun the request")
                result = PipelineResult(
                    run_id=run_id,
                    response=rewritten,
                    cards=catalog_cards(rewritten.looks, catalog),
                )
        except Exception as exc:
            log["error"] = type(exc).__name__ + ": " + str(exc)
            result = PipelineResult(
                run_id=run_id,
                failed=True,
                response=StylingResponse(
                    message=(
                        "Сейчас не удалось подобрать проверенные образы. Попробуйте ещё раз позже."
                    ),
                ),
            )
        log["metrics"]["latency_seconds"] = round(time.monotonic() - started, 3)
        stage_costs = [
            metrics.get("cost")
            for name, metrics in log["metrics"].items()
            if name in ("intent", "retrieval", "stylist", "response")
        ]
        log["metrics"]["cost_usd"] = (
            str(sum((Decimal(cost) for cost in stage_costs), Decimal("0")))
            if not result.failed and stage_costs and all(cost is not None for cost in stage_costs)
            else None
        )
        await self.repository.save_run(log)
        return result
