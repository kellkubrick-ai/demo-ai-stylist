import asyncio
import json

from ai_stylist.llm.intent import usage_dict
from ai_stylist.schemas.enrichment import EnrichmentRequest


def format_trace_markdown(row: dict) -> str:
    """Render one enrichment trace as a readable Markdown section."""
    trace_input = row.get("input") or {}
    product = trace_input.get("product") or {}
    title = str(product.get("title") or row["product_id"]).replace("\n", " ").strip()
    lines = [
        f"## {title}",
        "",
        f"- Product ID: `{row['product_id']}`",
        f"- Статус: `{row.get('status', 'unknown')}`",
        f"- Модель: `{row.get('model_id') or '—'}`",
    ]

    if trace_input:
        lines.extend(
            [
                f"- Reasoning effort: `{trace_input.get('reasoning_effort') or '—'}`",
                "",
                "### Вход модели",
                "",
                "#### Инструкция",
                "",
                "````text",
                str(trace_input.get("instructions") or ""),
                "````",
                "",
                "#### Данные товара",
                "",
                "````json",
                json.dumps(product, ensure_ascii=False, indent=2),
                "````",
                "",
                "#### Изображения",
                "",
            ]
        )
        images = trace_input.get("images") or []
        if images:
            for number, image in enumerate(images, start=1):
                url = image.get("url", "")
                lines.extend(
                    [
                        f"Фото {number}: [{url}]({url})",
                        "",
                        f"![Фото {number}]({url})",
                        "",
                    ]
                )
        else:
            lines.extend(["Изображения не передавались.", ""])

    if row.get("output") is not None:
        lines.extend(
            [
                "### Выход модели",
                "",
                "````json",
                json.dumps(row["output"], ensure_ascii=False, indent=2),
                "````",
                "",
            ]
        )
    if row.get("usage"):
        lines.extend(
            [
                "### Usage",
                "",
                "````json",
                json.dumps(row["usage"], ensure_ascii=False, indent=2),
                "````",
                "",
            ]
        )
    if row.get("error"):
        lines.extend(
            [
                "### Ошибка",
                "",
                "````text",
                str(row["error"]),
                "````",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def append_trace(report_path, row: dict) -> None:
    is_markdown = report_path.suffix.lower() == ".md"
    is_empty = not report_path.exists() or report_path.stat().st_size == 0
    with report_path.open("a", encoding="utf-8") as output:
        if is_markdown:
            if is_empty:
                output.write("# Лог VLM-обогащения каталога\n\n")
            output.write(format_trace_markdown(row))
        else:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")


class EnrichmentService:
    def __init__(
        self,
        agent,
        renderer,
        images,
        repository,
        model_id: str,
        reasoning_effort: str | None = None,
    ):
        self.agent = agent
        self.renderer = renderer
        self.images = images
        self.repository = repository
        self.model_id = model_id
        self.reasoning_effort = reasoning_effort
        self.last_usage = {}

    async def enrich(self, record, *, force=False) -> dict:
        if record.enrichment is not None and not force:
            return {
                "status": "reused",
                "model_id": self.model_id,
                "input": None,
                "output": record.enrichment.model_dump(mode="json"),
                "usage": {},
                "error": None,
            }
        group = record.product
        trace_input = None
        trace_output = None
        trace_usage = {}
        try:
            urls, photos = await self.images.select(group.id, group.image_urls)
            request = EnrichmentRequest(
                product_id=group.id,
                title=group.title,
                description=group.description,
                product_type=group.product_type,
                source_category_names=[c.name for c in group.source_categories],
                color=group.color,
                catalog_attributes=group.catalog_attributes,
                image_urls=urls,
            )
            knowledge = self.renderer.knowledge()
            trace_input = {
                "instructions": self.renderer.render("enrichment", knowledge),
                "reasoning_effort": self.reasoning_effort,
                "product": request.model_dump(mode="json"),
                "images": [
                    {"product_id": group.id, "position": position, "url": url}
                    for position, url in enumerate(urls)
                ],
            }
            content = [request.model_dump_json(), *photos]
            result = await self.agent.run(content, deps=knowledge)
            usage = usage_dict(result)
            self.last_usage = usage
            trace_output = result.output.model_dump(mode="json")
            trace_usage = usage
            await self.repository.save_enrichment(group.id, result.output, self.model_id)
            for url in urls:
                await self.repository.mark_image(group.id, url, True)
            return {
                "status": "enriched",
                "model_id": self.model_id,
                "input": trace_input,
                "output": trace_output,
                "usage": usage,
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "model_id": self.model_id,
                "input": trace_input,
                "output": trace_output,
                "usage": trace_usage,
                "error": type(exc).__name__ + ": " + str(exc),
            }


async def prepare_catalog(
    repository,
    service,
    report_path,
    *,
    force=False,
    concurrency=2,
    limit=None,
    product_ids=None,
    log_console=False,
):
    counts = {"enriched": 0, "reused": 0, "failed": 0}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tasks = set()

    async def process(record):
        try:
            trace = await service.enrich(record, force=force)
        except Exception as exc:
            trace = {
                "status": "failed",
                "model_id": None,
                "input": None,
                "output": None,
                "usage": {},
                "error": type(exc).__name__ + ": " + str(exc),
            }
        status = trace["status"]
        counts[status] += 1
        row = {"product_id": record.product.id, **trace}
        # Each completed row is persisted so interruption never loses the failure report.
        append_trace(report_path, row)
        if log_console:
            print(json.dumps(row, ensure_ascii=False))

    seen = 0
    async for record in repository.iter_products():
        if product_ids is not None and record.product.id not in product_ids:
            continue
        tasks.add(asyncio.create_task(process(record)))
        if len(tasks) >= concurrency:
            _, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        seen += 1
        if limit is not None and seen >= limit:
            break
    if tasks:
        await asyncio.gather(*tasks)
    return counts
