import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from alembic import command
from alembic.config import Config

from ai_stylist.bot.service import run_bot
from ai_stylist.catalog.embeddings import rebuild_embeddings
from ai_stylist.catalog.enrichment.service import EnrichmentService, prepare_catalog
from ai_stylist.catalog.importer import import_feed, inspect_feed, save_report
from ai_stylist.catalog.mapping import FeedMapping
from ai_stylist.catalog.repository import CatalogRepository
from ai_stylist.catalog.snapshot import export_snapshot, restore_snapshot
from ai_stylist.config import Settings
from ai_stylist.db import create_engine
from ai_stylist.evaluation import evaluate, record_review
from ai_stylist.pipeline.factory import make_embedder, runtime
from ai_stylist.providers.agents import make_agent
from ai_stylist.providers.images import ImageLoader
from ai_stylist.providers.prompts import PromptRenderer
from ai_stylist.schemas.enrichment import ProductEnrichment


def parser():
    root = argparse.ArgumentParser(description="Catalog-grounded fashion stylist demo")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply PostgreSQL/pgvector migrations")
    inspect = commands.add_parser(
        "inspect", help="Inspect bounded raw XML elements without importing"
    )
    inspect.add_argument("source", help="XML path or URL")
    inspect.add_argument("--offer-tag", required=True)
    inspect.add_argument("--category-tag", required=True)
    inspect.add_argument("--limit", type=int, default=5)
    importer = commands.add_parser(
        "import", help="Import all valid rows using a verified XML mapping"
    )
    importer.add_argument("source")
    importer.add_argument("--mapping", type=Path, required=True)
    importer.add_argument("--report", type=Path)
    importer.add_argument(
        "--all-groups",
        action="store_true",
        help="Disable active-group selection, not offer filters",
    )
    commands.add_parser("catalog-report")
    for name in ("enrich", "embeddings"):
        preparation = commands.add_parser(name)
        preparation.add_argument(
            "--force", action="store_true", help="Explicitly rebuild successes"
        )
        preparation.add_argument("--report", type=Path)
        preparation.add_argument("--limit", type=int, help="Model smoke test; unset processes all")
        preparation.add_argument(
            "--selection", type=Path, help="JSON manifest of product IDs to process"
        )
        if name == "enrich":
            preparation.add_argument(
                "--log-console",
                action="store_true",
                help="Print each per-product VLM trace as JSON",
            )
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore", help="Restore catalog snapshot into an empty database")
    restore.add_argument("snapshot", type=Path)
    style = commands.add_parser("style")
    style.add_argument("request", nargs="?")
    style.add_argument("--dialogue", type=Path, help="JSON list of role/content dialogue messages")
    evaluation = commands.add_parser("eval")
    evaluation.add_argument("--scenarios", type=Path, default=Path("evals/scenarios/demo.json"))
    evaluation.add_argument("--results", type=Path, default=Path("evals/results"))
    review = commands.add_parser("review", help="Record a human stylist verdict")
    review.add_argument("result", type=Path)
    review.add_argument("--verdict", choices=("approve", "revise", "reject"), required=True)
    review.add_argument("--comments", required=True)
    commands.add_parser("bot", help="Run Telegram long polling")
    return root


def feed_path(source: str, settings: Settings) -> Path:
    if not source.startswith(("https://", "http://")):
        return Path(source)
    directory = settings.data_dir / "feeds"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"download-{uuid4()}.xml"
    digest = hashlib.sha256()
    try:
        with httpx.stream(
            "GET", source, follow_redirects=True, timeout=settings.request_timeout_seconds
        ) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    digest.update(chunk)
                    output.write(chunk)
        target = directory / f"{digest.hexdigest()}.xml"
        if target.exists():
            temporary.unlink()
        else:
            temporary.rename(target)
        return target
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def load_selection(path: Path) -> tuple[str, set[str]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
        raise ValueError("Selection must contain an items list")
    version = manifest.get("catalog_version")
    if not isinstance(version, str) or not version:
        raise ValueError("Selection requires a catalog_version")
    ids = [item.get("product_id") for item in manifest["items"] if isinstance(item, dict)]
    if not ids or len(ids) != len(manifest["items"]):
        raise ValueError("Selection requires product IDs")
    try:
        ids = [str(UUID(value)) for value in ids]
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Selection contains an invalid product ID") from exc
    if len(set(ids)) != len(ids):
        raise ValueError("Selection contains duplicate product IDs")
    return version, set(ids)


async def execute(args, settings, source_path=None):
    if args.command in ("style", "eval", "bot"):
        if args.command == "bot":
            settings.require("telegram_bot_token")
        async with runtime(settings) as pipeline:
            if args.command == "bot":
                await run_bot(settings, pipeline)
                return {"stopped": True}
            if args.command == "eval":
                return await evaluate(pipeline, args.scenarios, args.results)
            if args.dialogue:
                dialogue = json.loads(args.dialogue.read_text(encoding="utf-8"))
            elif args.request:
                dialogue = [{"role": "user", "content": args.request}]
            else:
                raise ValueError("Supply a request or --dialogue")
            if (
                not dialogue
                or any(
                    not isinstance(item, dict)
                    or item.get("role") not in ("user", "assistant")
                    or not isinstance(item.get("content"), str)
                    for item in dialogue
                )
                or dialogue[-1]["role"] != "user"
            ):
                raise ValueError(
                    "Dialogue requires role/content entries ending with a user message"
                )
            result = await pipeline.run(dialogue)
            if result.failed:
                print(result.model_dump_json(indent=2))
                raise ValueError(f"Styling failed; inspect styling_runs for run_id {result.run_id}")
            return result.model_dump(mode="json")
    engine = create_engine(settings)
    try:
        repository = CatalogRepository(engine)
        if args.command == "import":
            mapping = FeedMapping.model_validate_json(args.mapping.read_text(encoding="utf-8"))
            if args.all_groups:
                mapping = mapping.model_copy(update={"active_groups_only": False})
            report = await import_feed(repository, source_path, mapping, args.source)
            path = args.report or settings.data_dir / "reports" / f"import-{report['version']}.json"
            save_report(path, report)
            report["report_path"] = str(path)
            return report
        if args.command == "catalog-report":
            return await repository.report()
        if args.command == "snapshot":
            return await export_snapshot(repository, args.output, settings.embedding_dimensions)
        if args.command == "restore":
            return await restore_snapshot(repository, args.snapshot, settings.embedding_dimensions)
        product_ids = None
        if args.selection:
            version, product_ids = load_selection(args.selection)
            if version != await repository.version():
                raise ValueError("Selection catalog_version does not match the current catalog")
            existing = await repository.get_products(list(product_ids))
            if {record.product.id for record in existing} != product_ids:
                raise ValueError("Selection contains product IDs missing from the catalog")
        if args.report:
            report_path = args.report
        elif args.command == "enrich":
            report_path = settings.data_dir / "logs" / "enrichment" / f"enrich-{uuid4()}.md"
        else:
            report_path = settings.data_dir / "reports" / f"{args.command}-{uuid4()}.jsonl"
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            if args.command == "embeddings":
                return await rebuild_embeddings(
                    repository,
                    make_embedder(settings, client),
                    report_path,
                    force=args.force,
                    limit=args.limit,
                    product_ids=product_ids,
                )
            renderer = PromptRenderer(settings.knowledge_dir)
            agent = make_agent(settings, "enrichment", ProductEnrichment, renderer)
            try:
                service = EnrichmentService(
                    agent,
                    renderer,
                    ImageLoader(client, settings.image_max_bytes, repository),
                    repository,
                    settings.enrichment_model,
                    settings.enrichment_reasoning_effort,
                )
                result = await prepare_catalog(
                    repository,
                    service,
                    report_path,
                    force=args.force,
                    concurrency=settings.offline_concurrency,
                    limit=args.limit,
                    product_ids=product_ids,
                    log_console=args.log_console,
                )
                result["report_path"] = str(report_path)
                return result
            finally:
                await agent.model.client.close()
    finally:
        await engine.dispose()


def main():
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    args = parser().parse_args()
    settings = Settings()
    try:
        if getattr(args, "limit", None) is not None and args.limit < 1:
            raise ValueError("Limit must be positive")
        if args.command == "migrate":
            command.upgrade(Config("alembic.ini"), "head")
            result = {"migrated": True}
        elif args.command == "review":
            record_review(args.result, args.verdict, args.comments)
            result = {"reviewed": str(args.result), "verdict": args.verdict}
        elif args.command == "inspect":
            if args.limit < 1 or args.limit > 100:
                raise ValueError("Inspection limit must be in [1, 100]")
            result = inspect_feed(
                feed_path(args.source, settings), args.offer_tag, args.category_tag, args.limit
            )
        else:
            source = feed_path(args.source, settings) if args.command == "import" else None
            result = asyncio.run(execute(args, settings, source))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except (ValueError, OSError, httpx.HTTPError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
