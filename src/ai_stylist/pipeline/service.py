import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import Field
from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessagesTypeAdapter

from ai_stylist.retriever.service import covered_plan
from ai_stylist.schemas.base import Schema
from ai_stylist.schemas.retrieval import RetrievalRequest
from ai_stylist.schemas.styling import ResponseRequest, StylingResponse, VLMStylingResponse
from ai_stylist.stylist.service import build_styling_request
from ai_stylist.stylist.validation import validate_styling

logger = logging.getLogger(__name__)


def start_trace_step(log, step):
    started = time.monotonic()
    log["metrics"]["trace"].append(
        {"step": step, "status": "started", "at": datetime.now(UTC).isoformat()}
    )
    return started


def finish_trace_step(log, step, started, status="completed", **details):
    log["metrics"]["trace"].append(
        {
            "step": step,
            "status": status,
            "at": datetime.now(UTC).isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            **details,
        }
    )


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
    def __init__(
        self,
        repository,
        intent,
        retriever,
        stylist,
        response,
        renderer,
        max_products=30,
        trace_dir: Path | None = None,
    ):
        self.repository = repository
        self.intent = intent
        self.retriever = retriever
        self.stylist = stylist
        self.response = response
        self.renderer = renderer
        self.max_products = max_products
        self.trace_dir = trace_dir

    async def run(
        self, dialogue: list[dict], *, session_id=None, on_search_start=None
    ) -> PipelineResult:
        run_id = str(uuid4())
        started = time.monotonic()
        log = {
            "id": run_id,
            "session_id": session_id,
            "user_request": dialogue[-1]["content"],
            "catalog_version": await self.repository.version(),
            "intent_json": {},
            "outfit_plan_json": {},
            "metrics": {"dialogue": dialogue, "trace": []},
        }
        current_step = "intent_planning"
        step_started = start_trace_step(log, current_step)
        try:
            with capture_run_messages() as intent_messages:
                try:
                    understanding = await self.intent.understand(dialogue)
                except Exception:
                    log["metrics"]["intent_model_messages"] = (
                        ModelMessagesTypeAdapter.dump_python(intent_messages, mode="json")
                    )
                    raise
            intent, plan = understanding.intent, understanding.outfit_plan
            log.update(
                intent_json=intent.model_dump(mode="json"),
                outfit_plan_json=plan.model_dump(mode="json"),
            )
            log["metrics"]["intent"] = self.intent.last_usage
            finish_trace_step(
                log,
                current_step,
                step_started,
                needs_clarification=intent.needs_clarification,
                has_dialogue_reply=understanding.dialogue_reply is not None,
                composition_count=len(plan.compositions),
            )
            if session_id and understanding.dialogue_reply is None:
                await self.repository.update_session(
                    session_id,
                    current_intent=log["intent_json"],
                    current_outfit_plan=log["outfit_plan_json"],
                )
            if understanding.dialogue_reply is not None:
                current_step = "dialogue_reply"
                step_started = start_trace_step(log, current_step)
                log["result_json"] = {"dialogue_reply": understanding.dialogue_reply}
                result = PipelineResult(
                    run_id=run_id,
                    response=StylingResponse(message=understanding.dialogue_reply),
                )
                finish_trace_step(log, current_step, step_started)
            elif intent.needs_clarification:
                current_step = "clarification"
                step_started = start_trace_step(log, current_step)
                result = PipelineResult(
                    run_id=run_id,
                    clarification=True,
                    response=StylingResponse(message=intent.clarification_question),
                )
                finish_trace_step(log, current_step, step_started)
            else:
                current_step = "retrieval"
                step_started = start_trace_step(log, current_step)
                if on_search_start is not None:
                    await on_search_start()
                pool = await self.retriever.retrieve(
                    RetrievalRequest(
                        intent=intent,
                        outfit_plan=plan,
                        max_products=self.max_products,
                    )
                )
                log["metrics"]["retrieval"] = self.retriever.last_usage
                log["metrics"]["candidate_pool"] = pool.model_dump(mode="json")
                finish_trace_step(
                    log,
                    current_step,
                    step_started,
                    candidate_count=len(pool.products),
                    missing_required_slots=[
                        missing.model_dump(mode="json")
                        for missing in pool.missing_required_slots
                    ],
                )
                current_step = "plan_coverage"
                step_started = start_trace_step(log, current_step)
                viable = covered_plan(pool, plan, intent)
                finish_trace_step(
                    log,
                    current_step,
                    step_started,
                    viable_plan_ids=[
                        composition.plan_id for composition in viable.compositions
                    ],
                )
                if not viable.compositions:
                    current_step = "styling_skipped"
                    step_started = start_trace_step(log, current_step)
                    role_labels = {
                        "top": "верха",
                        "bottom": "низа",
                        "dress": "платья или комбинезона",
                        "set": "комплекта",
                        "outer_layer": "верхнего слоя",
                        "shoes": "обуви",
                        "bag": "сумки",
                        "accessory": "аксессуаров",
                    }
                    roles = ", ".join(
                        dict.fromkeys(
                            role_labels[missing.slot]
                            for missing in pool.missing_required_slots
                        )
                    )
                    limitation = "Не удалось собрать полный образ"
                    if roles:
                        limitation += f": в каталоге не нашлись подходящие товары для {roles}"
                    styled = VLMStylingResponse(limitations=[limitation + "."])
                    catalog = []
                    finish_trace_step(
                        log, current_step, step_started, reason="No viable required-role coverage"
                    )
                else:
                    current_step = "styling_and_validation"
                    step_started = start_trace_step(log, current_step)
                    request = build_styling_request(intent, plan, pool, self.renderer.knowledge())
                    log["vlm_request_json"] = request.model_dump(mode="json")
                    styled = await self.stylist.style(request)
                    log["metrics"]["stylist"] = self.stylist.last_usage
                    catalog = await self.repository.get_products(
                        [p.product_id for p in request.products],
                        usable_images_only=True,
                    )
                    validate_styling(request, styled, catalog)
                    finish_trace_step(
                        log,
                        current_step,
                        step_started,
                        candidate_count=len(request.products),
                        validated_look_count=len(styled.looks),
                    )
                log["result_json"] = styled.model_dump(mode="json")
                current_step = "response_rewrite"
                step_started = start_trace_step(log, current_step)
                rewritten = await self.response.respond(
                    ResponseRequest(
                        intent=intent,
                        styling_result=styled,
                    )
                )
                log["metrics"]["response"] = self.response.last_usage
                finish_trace_step(
                    log,
                    current_step,
                    step_started,
                    look_count=len(rewritten.looks),
                    has_follow_up=bool(rewritten.follow_up_question),
                )
                current_step = "catalog_version_check"
                step_started = start_trace_step(log, current_step)
                if await self.repository.version() != log["catalog_version"]:
                    raise ValueError("Catalog version changed during styling; rerun the request")
                finish_trace_step(log, current_step, step_started)
                result = PipelineResult(
                    run_id=run_id,
                    response=rewritten,
                    cards=catalog_cards(rewritten.looks, catalog),
                )
        except Exception as exc:
            log["error"] = type(exc).__name__ + ": " + str(exc)
            finish_trace_step(
                log,
                current_step,
                step_started,
                status="failed",
                error=log["error"],
            )
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
        if self.trace_dir:
            trace_path = self.trace_dir / f"{run_id}.json"
            log["metrics"]["trace_file"] = str(trace_path)
            try:
                trace_path.parent.mkdir(parents=True, exist_ok=True)
                trace_path.write_text(
                    json.dumps(log, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8",
                )
            except OSError:
                logger.exception("Could not write styling trace to %s", trace_path)
        await self.repository.save_run(log)
        return result
