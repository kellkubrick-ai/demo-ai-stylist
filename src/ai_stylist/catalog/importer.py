import hashlib
import json
from collections import Counter
from pathlib import Path

from defusedxml.ElementTree import iterparse

from ai_stylist.catalog.mapping import FeedMapping, scalar, stable_id, values
from ai_stylist.schemas.catalog import CatalogCategory


def xml_elements(path: Path, tag: str):
    """Yield complete elements, detaching them after consumption to bound memory."""
    stack = []
    target_depth = 0
    for event, element in iterparse(path, events=("start", "end")):
        if event == "start":
            stack.append(element)
            if element.tag == tag:
                target_depth += 1
            continue
        if element.tag == tag:
            yield element
            target_depth -= 1
        # Also discard unrelated subtrees (e.g. offers during the category pass).
        # Children of a requested element remain intact until that element is yielded.
        if element.tag == tag or target_depth == 0:
            element.clear()
            if len(stack) > 1:
                stack[-2].remove(element)
        stack.pop()


def inspect_feed(path: Path, offer_tag: str, category_tag: str, limit: int = 5) -> dict:
    from xml.etree.ElementTree import tostring

    result = {}
    for key, tag in (("categories", category_tag), ("offers", offer_tag)):
        samples = []
        for element in xml_elements(path, tag):
            samples.append(tostring(element, encoding="unicode"))
            if len(samples) >= limit:
                break
        result[key] = samples
    return result


async def import_feed(repository, path: Path, mapping: FeedMapping, source: str) -> dict:
    mapping.assert_verified()
    known_categories = {}
    for element in xml_elements(path, mapping.category_tag):
        category = mapping.category(element)
        if category.external_id in known_categories:
            if known_categories[category.external_id] != category:
                raise ValueError("Conflicting category definitions")
        known_categories[category.external_id] = category
    known_ids = {c.id for c in known_categories.values()}
    if any(c.parent_id and c.parent_id not in known_ids for c in known_categories.values()):
        raise ValueError("Source category parent is not defined")
    mapping_bytes = mapping.model_dump_json().encode()
    with path.open("rb") as feed_file:
        feed_hash = hashlib.file_digest(feed_file, "sha256").hexdigest()
    mapping_hash = hashlib.sha256(mapping_bytes).hexdigest()
    version = hashlib.sha256((feed_hash + mapping_hash).encode()).hexdigest()
    report = {"version": version, "source": source, "offers": 0, "products": 0, "errors": []}
    active_groups = None
    if mapping.active_groups_only:
        active_groups = set()
        for element in xml_elements(path, mapping.offer_tag):
            try:
                if mapping.availability.evaluate(element):
                    group_id = scalar(element, mapping.fields["visual_id"])
                    if group_id:
                        active_groups.add(group_id)
            except (ValueError, ArithmeticError):
                continue
        if not active_groups:
            raise ValueError("No source groups have verified available offers")
        report["selected_active_groups"] = len(active_groups)
        report["skipped_inactive_offers"] = 0
    undefined_categories = Counter()
    seen_offers = set()
    identities = {}
    images = {}
    counts = Counter()
    async with repository.engine.begin() as connection:
        await repository.import_categories(connection, list(known_categories.values()))
        await repository.begin_import(connection)
        for position, element in enumerate(xml_elements(path, mapping.offer_tag), 1):
            try:
                if active_groups is not None:
                    visual_id = scalar(element, mapping.fields["visual_id"])
                    if visual_id not in active_groups:
                        report["skipped_inactive_offers"] += 1
                        continue
                referenced = {
                    category_id
                    for category_path in mapping.category_paths
                    for category_id in values(element, category_path)
                }
                missing = referenced - known_categories.keys()
                if missing:
                    placeholders = [
                        CatalogCategory(
                            id=stable_id("category", category_id),
                            external_id=category_id,
                            name=f"[Название отсутствует в XML: {category_id}]",
                        )
                        for category_id in sorted(missing)
                    ]
                    await repository.import_categories(connection, placeholders)
                    known_categories.update({item.external_id: item for item in placeholders})
                undefined_categories.update(
                    category_id
                    for category_id in referenced
                    if known_categories[category_id].name.startswith("[Название отсутствует в XML:")
                )
                product = mapping.product(element, known_categories)
                offer = product.offers[0]
                if offer.id in seen_offers:
                    raise ValueError("Duplicate offer identifier in source feed")
                group = product.product
                identity = (group.title, group.color, group.source_model_id, group.product_type)
                if group.id in identities and identities[group.id] != identity:
                    raise ValueError("Conflicting facts for one visual grouping key")
                first = group.id not in identities
                await repository.import_product(connection, product, first)
                identities[group.id] = identity
                urls = images.setdefault(group.id, [])
                urls.extend(str(url) for url in group.image_urls if str(url) not in urls)
                seen_offers.add(offer.id)
                report["offers"] += 1
                if first:
                    counts[group.product_type or "unmapped"] += 1
            except (ValueError, ArithmeticError) as exc:
                offer_ids = values(element, mapping.fields["offer_id"])
                report["errors"].append(
                    {
                        "position": position,
                        "offer_id": offer_ids[0] if len(offer_ids) == 1 else offer_ids,
                        "error": str(exc),
                    }
                )
        if not seen_offers:
            raise ValueError("No valid offers; catalog update rolled back")
        report["products"] = len(identities)
        report["types"] = dict(counts)
        report["undefined_category_ids"] = dict(undefined_categories)
        await repository.sync_images(connection, images)
        await repository.finish_import(connection, version, source, report)
    return report


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
