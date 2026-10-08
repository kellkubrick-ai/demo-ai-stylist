"""Summarize source XML facts before approving a store-specific catalog mapping."""

import argparse
import json
from collections import Counter
from pathlib import Path

from ai_stylist.catalog.importer import xml_elements


def child_text(element, tag: str) -> str | None:
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else None


def profile(path: Path) -> dict:
    categories = {
        element.attrib["id"]: {
            "name": (element.text or "").strip(),
            "parent_id": element.get("parentId"),
        }
        for element in xml_elements(path, "category")
    }
    counters = {
        name: Counter()
        for name in (
            "attributes",
            "children",
            "params",
            "currency",
            "availability",
            "gender",
            "size",
            "category",
            "pictures_per_offer",
            "categories_per_offer",
        )
    }
    missing = Counter()
    group_facts = {}
    undefined_categories = Counter()
    available_category_samples = {}
    group_conflicts = []
    availability_conflicts = []
    samples = {"available": [], "unavailable": []}
    offers = 0
    for element in xml_elements(path, "offer"):
        offers += 1
        counters["attributes"].update(element.attrib.keys())
        children = list(element)
        counters["children"].update(child.tag for child in children)
        params = {
            child.get("code"): (child.text or "").strip()
            for child in children
            if child.tag == "param"
        }
        counters["params"].update(params.keys())
        counters["currency"].update([child_text(element, "currencyId")])
        available = element.get("available")
        counters["availability"].update([available])
        counters["gender"].update([params.get("gender")])
        counters["size"].update([params.get("size")])
        category_ids = [child.text for child in children if child.tag == "categoryId"]
        counters["category"].update(category_ids)
        undefined_categories.update(cid for cid in category_ids if cid not in categories)
        counters["categories_per_offer"].update([len(category_ids)])
        pictures = [child.text for child in children if child.tag == "picture"]
        counters["pictures_per_offer"].update([len(pictures)])
        for field, value in (
            ("offer_id", element.get("id")),
            ("group_id", element.get("group_id")),
            ("model", child_text(element, "model")),
            ("color", params.get("color")),
            ("size", params.get("size")),
            ("price", child_text(element, "price")),
            ("photo", pictures[0] if pictures else None),
        ):
            if not value:
                missing[field] += 1
        group_id = element.get("group_id")
        identity = (
            child_text(element, "model"),
            params.get("color"),
            child_text(element, "article"),
        )
        if group_id:
            previous = group_facts.setdefault(group_id, identity)
            if previous != identity and len(group_conflicts) < 10:
                group_conflicts.append({"group_id": group_id, "first": previous, "later": identity})
        balance = child_text(element, "balance")
        if balance is not None and len(availability_conflicts) < 10:
            try:
                has_stock = float(balance) > 0
            except ValueError:
                has_stock = None
            if has_stock is not None and (available == "true") != has_stock:
                availability_conflicts.append(
                    {
                        "offer_id": element.get("id"),
                        "available": available,
                        "balance": balance,
                    }
                )
        label = "available" if available == "true" else "unavailable"
        if available == "true":
            for category_id in category_ids:
                if "Комплект" in categories.get(category_id, {}).get("name", ""):
                    examples = available_category_samples.setdefault(category_id, [])
                    if len(examples) < 12:
                        examples.append(
                            {
                                "offer_id": element.get("id"),
                                "model": identity[0],
                                "article": identity[2],
                                "price": child_text(element, "price"),
                                "category_ids": category_ids,
                            }
                        )
        if len(samples[label]) < 3:
            samples[label].append(
                {
                    "id": element.get("id"),
                    "group_id": group_id,
                    "model": identity[0],
                    "color": identity[1],
                    "size": params.get("size"),
                    "price": child_text(element, "price"),
                    "balance": balance,
                    "categories": category_ids,
                    "pictures": pictures[:2],
                }
            )
    return {
        "source_file": str(path),
        "category_definitions": len(categories),
        "undefined_category_ids": dict(undefined_categories),
        "available_category_samples": available_category_samples,
        "offer_count": offers,
        "visual_group_count": len(group_facts),
        "missing_required_fields": dict(missing),
        "group_fact_conflict_examples": group_conflicts,
        "availability_balance_conflict_examples": availability_conflicts,
        "fields": {
            name: dict(counter.most_common(60))
            for name, counter in counters.items()
            if name != "category"
        },
        "categories": [
            {"id": category_id, **categories.get(category_id, {}), "offers": count}
            for category_id, count in counters["category"].most_common()
        ],
        "samples": samples,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("feed", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = profile(args.feed)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "offer_count",
                    "visual_group_count",
                    "missing_required_fields",
                    "group_fact_conflict_examples",
                    "availability_balance_conflict_examples",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
