"""Dry-run a verified feed mapping over every offer before touching the database."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from ai_stylist.catalog.importer import xml_elements
from ai_stylist.catalog.mapping import FeedMapping, stable_id, values
from ai_stylist.schemas.catalog import CatalogCategory


def validate(path: Path, mapping: FeedMapping) -> dict:
    mapping.assert_verified()
    categories = {
        category.external_id: category
        for category in (
            mapping.category(element) for element in xml_elements(path, mapping.category_tag)
        )
    }
    counts = Counter()
    errors = Counter()
    error_examples = defaultdict(list)
    group_facts = {}
    group_offer_counts = Counter()
    available_group_details = {}
    available_groups = set()
    undefined_category_ids = set()
    for position, element in enumerate(xml_elements(path, mapping.offer_tag), 1):
        for category_path in mapping.category_paths:
            for category_id in values(element, category_path):
                if category_id not in categories:
                    categories[category_id] = CatalogCategory(
                        id=stable_id("category", category_id),
                        external_id=category_id,
                        name=f"[Название отсутствует в XML: {category_id}]",
                    )
                    undefined_category_ids.add(category_id)
        try:
            record = mapping.product(element, categories)
        except (ValueError, ArithmeticError) as exc:
            label = str(exc)
            errors[label] += 1
            if len(error_examples[label]) < 3:
                error_examples[label].append({"position": position, "offer_id": element.get("id")})
            continue
        group = record.product
        identity = (group.title, group.color, group.source_model_id, group.product_type)
        previous = group_facts.setdefault(group.id, identity)
        if previous != identity:
            label = "Conflicting facts within a visual group"
            errors[label] += 1
            if len(error_examples[label]) < 3:
                error_examples[label].append({"position": position, "offer_id": element.get("id")})
            continue
        counts["valid_offers"] += 1
        group_offer_counts[group.id] += 1
        if record.offers[0].available:
            counts["available_offers"] += 1
            available_groups.add(group.id)
            available_group_details.setdefault(
                group.id,
                {
                    "title": group.title,
                    "category_ids": [category.external_id for category in group.source_categories],
                },
            )
    eligible_types = Counter(
        group_facts[group_id][3] or "unmapped" for group_id in available_groups
    )
    unmapped_categories = Counter()
    unmapped_examples = []
    for group_id in available_groups:
        if group_facts[group_id][3] is not None:
            continue
        detail = available_group_details[group_id]
        unmapped_categories.update(detail["category_ids"])
        if len(unmapped_examples) < 12:
            unmapped_examples.append(detail)
    return {
        "valid_offers": counts["valid_offers"],
        "available_offers": counts["available_offers"],
        "visual_groups": len(group_facts),
        "available_visual_groups": len(available_groups),
        "offers_in_available_groups": sum(group_offer_counts[g] for g in available_groups),
        "available_groups_by_type": dict(eligible_types),
        "unmapped_category_counts": dict(unmapped_categories.most_common(40)),
        "unmapped_examples": unmapped_examples,
        "undefined_category_ids": sorted(undefined_category_ids),
        "errors": dict(errors),
        "error_examples": dict(error_examples),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("feed", type=Path)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    mapping = FeedMapping.model_validate_json(args.mapping.read_text(encoding="utf-8"))
    report = validate(args.feed, mapping)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key
                not in (
                    "undefined_category_ids",
                    "error_examples",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
