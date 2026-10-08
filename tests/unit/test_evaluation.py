import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ai_stylist.evaluation import evaluate, record_review
from ai_stylist.pipeline.service import PipelineResult, catalog_cards
from ai_stylist.schemas.styling import StylingResponse


async def test_eval_records_constraints_and_requires_human_review(
    tmp_path, records, styling_result
):
    scenarios = tmp_path / "scenarios.json"
    scenarios.write_text(
        json.dumps(
            [
                {
                    "scenario_id": "budget",
                    "user_request": "Образ до 500 рублей",
                    "target_looks": 1,
                    "expected_hard_constraints": {"budget": {"max_outfit_price": "500"}},
                }
            ]
        ),
        encoding="utf-8",
    )
    pipeline = AsyncMock()
    pipeline.run.return_value = PipelineResult(
        run_id="run",
        response=StylingResponse(
            message="Готово",
            looks=styling_result.looks,
            limitations=styling_result.limitations,
        ),
        cards=catalog_cards(styling_result.looks, records),
    )
    pipeline.repository.get_run.return_value = {"catalog_version": "frozen"}
    pipeline.repository.get_products.return_value = records
    result = await evaluate(pipeline, scenarios, tmp_path / "results")
    assert result["scenarios"][0]["hard_constraint_violations"] == [
        "Expected outfit budget exceeded"
    ]
    assert not result["scenarios"][0]["target_met"]
    output = Path(result["scenarios"][0]["path"])
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["stylist_verdict"] is None
    record_review(output, "reject", "Бюджет превышен.")
    assert json.loads(output.read_text(encoding="utf-8"))["stylist_verdict"] == "reject"


async def test_duplicate_scenario_ids_are_rejected_before_results_are_written(tmp_path):
    path = tmp_path / "scenarios.json"
    path.write_text(
        json.dumps(
            [
                {"scenario_id": "same", "user_request": "first"},
                {"scenario_id": "same", "user_request": "second"},
            ]
        ),
        encoding="utf-8",
    )
    pipeline = AsyncMock()
    with pytest.raises(ValueError, match="unique IDs"):
        await evaluate(pipeline, path, tmp_path / "results")
    pipeline.run.assert_not_awaited()
    assert not (tmp_path / "results").exists()
