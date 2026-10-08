import asyncio
import json
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from ai_stylist.retriever.query_builder import offer_matches, product_matches
from ai_stylist.schemas.base import Schema
from ai_stylist.schemas.intent import StylingIntent
from ai_stylist.schemas.planning import OutfitSlot


class EvalScenario(Schema):
    scenario_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    user_request: str
    expected_hard_constraints: dict = Field(default_factory=dict)
    target_looks: int = Field(default=2, ge=0, le=3)


async def evaluate(pipeline, scenarios_path: Path, results_dir: Path):
    source = await asyncio.to_thread(scenarios_path.read_text, encoding="utf-8")
    scenarios = [EvalScenario.model_validate(s) for s in json.loads(source)]
    ids = [scenario.scenario_id for scenario in scenarios]
    if not scenarios or len(ids) != len(set(ids)):
        raise ValueError("Evaluation requires nonempty scenarios with unique IDs")
    output_dir = results_dir / str(uuid4())
    output_dir.mkdir(parents=True)
    summary = []
    for scenario in scenarios:
        result = await pipeline.run([{"role": "user", "content": scenario.user_request}])
        run = await pipeline.repository.get_run(result.run_id)
        expected = StylingIntent.model_validate(scenario.expected_hard_constraints)
        ids = [item.product_id for look in result.response.looks for item in look.items]
        catalog = {r.product.id: r for r in await pipeline.repository.get_products(ids)}
        violations = []
        for look in result.response.looks:
            for item in look.items:
                record = catalog[item.product_id]
                offer = next(o for o in record.offers if o.id == item.offer_id)
                slot = OutfitSlot(role=item.slot, retrieval_query="evaluation")
                if not product_matches(record.product, expected, slot):
                    violations.append(f"{item.product_id}: expected catalog constraint violated")
                if not offer_matches(offer, expected, slot):
                    violations.append(f"{item.offer_id}: expected offer constraint violated")
        if expected.budget and expected.budget.max_outfit_price is not None:
            if any(cards.total > expected.budget.max_outfit_price for cards in result.cards):
                violations.append("Expected outfit budget exceeded")
        row = {
            **scenario.model_dump(mode="json"),
            "run": run,
            "result": result.model_dump(mode="json"),
            "hard_constraint_violations": violations,
            "target_met": (
                len(result.response.looks) >= scenario.target_looks
                and not result.failed
                and not violations
            ),
            "stylist_verdict": None,
            "stylist_comments": None,
        }
        path = output_dir / f"{scenario.scenario_id}.json"
        path.write_text(
            json.dumps(row, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        summary.append(
            {
                "scenario_id": scenario.scenario_id,
                "looks": len(result.response.looks),
                "failed": result.failed,
                "target_met": row["target_met"],
                "hard_constraint_violations": violations,
                "path": str(path),
            }
        )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"results": str(output_dir), "scenarios": summary}


def record_review(path: Path, verdict: str, comments: str):
    if verdict not in ("approve", "revise", "reject"):
        raise ValueError("Invalid stylist verdict")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["stylist_verdict"] = verdict
    data["stylist_comments"] = comments
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
