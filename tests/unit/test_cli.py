import json
from pathlib import Path

import pytest

from ai_stylist.cli import load_selection, parser


def test_full_catalog_import_is_explicit():
    arguments = ["import", "catalog.xml", "--mapping", "mapping.json"]
    assert parser().parse_args(arguments).all_groups is False
    assert parser().parse_args([*arguments, "--all-groups"]).all_groups is True


def test_preparation_accepts_curated_selection():
    for command in ("enrich", "embeddings"):
        args = parser().parse_args([command, "--selection", "evals/catalog/demo-100.json"])
        assert args.selection == Path("evals/catalog/demo-100.json")


def test_enrichment_can_log_model_io_to_console():
    args = parser().parse_args(["enrich", "--log-console"])
    assert args.log_console is True


def test_demo_selection_has_100_unique_product_groups():
    version, ids = load_selection(Path("evals/catalog/demo-100.json"))
    assert version and len(ids) == 100


def test_demo_search_probes_reference_selected_products():
    _, ids = load_selection(Path("evals/catalog/demo-100.json"))
    probes = json.loads(
        Path("evals/catalog/demo-100-search-probes.json").read_text(encoding="utf-8")
    )
    assert len(probes["cases"]) == 8
    for case in probes["cases"]:
        assert case["expected_product_ids"]
        assert set(case["expected_product_ids"] + case.get("excluded_product_ids", [])) <= ids


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"product_id": "not-a-uuid"}],
        [
            {"product_id": "0cabf0a2-4610-56ea-951c-65ec7046ea33"},
            {"product_id": "0CABF0A2-4610-56EA-951C-65EC7046EA33"},
        ],
    ],
)
def test_invalid_selection_is_rejected(tmp_path, items):
    path = tmp_path / "selection.json"
    path.write_text(json.dumps({"catalog_version": "v1", "items": items}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_selection(path)
