import asyncio
import json

from ai_stylist.llm.intent import usage_dict
from ai_stylist.schemas.enrichment import EnrichmentRequest


class EnrichmentService:
    def __init__(self, agent, renderer, images, repository, model_id: str):
        self.agent = agent
        self.renderer = renderer
        self.images = images
        self.repository = repository
        self.model_id = model_id
        self.last_usage = {}

    async def enrich(self, record, *, force=False) -> str:
        if record.enrichment is not None and not force:
            return "reused"
        group = record.product
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
        content = [request.model_dump_json(), *photos]
        result = await self.agent.run(content, deps=self.renderer.knowledge())
        self.last_usage = usage_dict(result)
        await self.repository.save_enrichment(group.id, result.output, self.model_id)
        return "enriched"


async def prepare_catalog(
    repository, service, report_path, *, force=False, concurrency=2, limit=None
):
    counts = {"enriched": 0, "reused": 0, "failed": 0}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tasks = set()

    async def process(record):
        try:
            status = await service.enrich(record, force=force)
            error = None
        except Exception as exc:
            status, error = "failed", type(exc).__name__ + ": " + str(exc)
        counts[status] += 1
        # Each completed row is persisted so interruption never loses the failure report.
        with report_path.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "product_id": record.product.id,
                        "status": status,
                        "error": error,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    seen = 0
    async for record in repository.iter_products():
        tasks.add(asyncio.create_task(process(record)))
        if len(tasks) >= concurrency:
            _, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        seen += 1
        if limit is not None and seen >= limit:
            break
    if tasks:
        await asyncio.gather(*tasks)
    return counts
