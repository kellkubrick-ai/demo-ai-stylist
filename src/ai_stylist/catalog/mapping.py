import re
from decimal import Decimal
from typing import Literal, Self
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, model_validator

from ai_stylist.schemas.base import Schema
from ai_stylist.schemas.catalog import CatalogCategory, CatalogProduct, Offer, ProductGroup
from ai_stylist.schemas.planning import PRODUCT_TYPES


def stable_id(kind: str, external_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"ai-stylist:{kind}:{external_id}"))


def values(element, selector: str) -> list[str]:
    if selector.startswith("@"):
        value = element.get(selector[1:])
        return [value] if value is not None else []
    if "/@" in selector:
        path, attribute = selector.rsplit("/@", 1)
        return [node.get(attribute) for node in element.findall(path) if node.get(attribute)]
    return ["".join(node.itertext()).strip() for node in element.findall(selector)]


def scalar(element, selector: str | None) -> str | None:
    found = values(element, selector) if selector else []
    if len(found) > 1:
        raise ValueError(f"Scalar mapping {selector!r} produced multiple values")
    return found[0] if found and found[0] else None


class AvailabilityRule(Schema):
    path: str
    operator: Literal["equals", "positive"]
    true_values: list[str] = Field(default_factory=list)
    false_values: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_rule(self) -> Self:
        if self.operator == "equals":
            if not self.true_values or not self.false_values:
                raise ValueError("Equality availability rules need true and false source values")
            if set(self.true_values) & set(self.false_values):
                raise ValueError("Availability true/false values overlap")
        return self

    def evaluate(self, element) -> bool:
        value = scalar(element, self.path)
        if value is None:
            raise ValueError(f"Missing verified availability field {self.path}")
        if self.operator == "positive":
            amount = Decimal(value)
            if not amount.is_finite() or amount < 0:
                raise ValueError("Invalid source stock quantity")
            return amount > 0
        if value in self.true_values:
            return True
        if value in self.false_values:
            return False
        raise ValueError(f"Unknown availability value for {self.path}")


class AttributeMapping(Schema):
    path: str
    kind: Literal["text", "bool", "decimal", "list"] = "text"
    true_values: list[str] = Field(default_factory=list)
    false_values: list[str] = Field(default_factory=list)

    def extract(self, element):
        if self.kind == "list":
            return values(element, self.path)
        value = scalar(element, self.path)
        if value is None:
            return None
        if self.kind == "bool":
            if value in self.true_values:
                return True
            if value in self.false_values:
                return False
            raise ValueError(f"Unmapped boolean attribute {self.path}")
        if self.kind == "decimal":
            number = Decimal(value)
            if not number.is_finite():
                raise ValueError("Non-finite catalog attribute")
            return str(number)
        return value


class TitleTypeRule(Schema):
    pattern: str
    product_type: str
    category_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def check_rule(self) -> Self:
        re.compile(self.pattern)
        if self.product_type not in PRODUCT_TYPES:
            raise ValueError("Title rule has an unsupported normalized product type")
        return self


class FeedMapping(Schema):
    verified: bool = False
    verification_notes: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    offer_tag: str
    category_tag: str
    category_fields: dict[str, str]
    fields: dict[str, str]
    grouping: Literal["visual_id", "model_color"]
    active_groups_only: bool = False
    category_paths: list[str] = Field(min_length=1)
    image_paths: list[str] = Field(min_length=1)
    availability: AvailabilityRule
    currency_map: dict[str, str]
    product_type_map: dict[str, str]
    product_type_path: str | None = None
    product_type_category_map: dict[str, str] = Field(default_factory=dict)
    product_type_category_priority: list[str] = Field(default_factory=list)
    product_type_title_rules: list[TitleTypeRule] = Field(default_factory=list)
    attributes: dict[str, AttributeMapping] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_mapping(self) -> Self:
        required = {"offer_id", "visual_id", "title", "price", "currency"}
        if not required <= self.fields.keys():
            raise ValueError(f"Missing field mappings: {sorted(required - self.fields.keys())}")
        if not {"id", "name"} <= self.category_fields.keys():
            raise ValueError("Category ID and name mappings are required")
        if self.grouping == "model_color" and "color" not in self.fields:
            raise ValueError("Model/color grouping needs a verified color field")
        if self.active_groups_only and self.grouping != "visual_id":
            raise ValueError("Active-group import requires a verified visual ID")
        mapped_types = {*self.product_type_map.values(), *self.product_type_category_map.values()}
        if not mapped_types <= set(PRODUCT_TYPES):
            raise ValueError("Mapping contains unsupported normalized product types")
        if self.product_type_category_priority:
            if len(self.product_type_category_priority) != len(
                set(self.product_type_category_priority)
            ):
                raise ValueError("Category type priority contains duplicate source IDs")
            if set(self.product_type_category_priority) != set(self.product_type_category_map):
                raise ValueError("Category type priority must list every mapped source ID")
        expected_kinds = {"features": "list", "bundle_confirmed": "bool", "audience": "text"}
        for name, kind in expected_kinds.items():
            if name in self.attributes and self.attributes[name].kind != kind:
                raise ValueError(f"Catalog attribute {name} requires mapping kind {kind}")
        return self

    def assert_verified(self) -> None:
        if not self.verified:
            raise ValueError("Feed mapping is not verified against raw XML; import refused")

    def category(self, element) -> CatalogCategory:
        external = scalar(element, self.category_fields["id"])
        name = scalar(element, self.category_fields["name"])
        if not external or not name:
            raise ValueError("Category requires source ID and name")
        parent = scalar(element, self.category_fields.get("parent_id"))
        return CatalogCategory(
            id=stable_id("category", external),
            external_id=external,
            name=name,
            parent_id=stable_id("category", parent) if parent else None,
        )

    def product(self, element, known_categories: dict[str, CatalogCategory]) -> CatalogProduct:
        facts = {name: scalar(element, path) for name, path in self.fields.items()}
        for key in ("offer_id", "visual_id", "title", "price", "currency"):
            if facts.get(key) is None:
                raise ValueError(f"Missing required source field: {key}")
        color = facts.get("color")
        if self.grouping == "model_color" and not color:
            raise ValueError("Cannot group a model by an unknown color")
        external = facts["visual_id"]
        if self.grouping == "model_color":
            external = f"{external}:{color}"
        product_id = stable_id("product", external)
        category_ids = list(
            dict.fromkeys(item for path in self.category_paths for item in values(element, path))
        )
        unknown = set(category_ids) - known_categories.keys()
        if unknown:
            raise ValueError(f"Unknown source categories: {sorted(unknown)}")
        raw_type = scalar(element, self.product_type_path)
        product_type = self.product_type_map.get(raw_type) if raw_type else None
        if not product_type and self.product_type_category_priority:
            product_type = next(
                (
                    self.product_type_category_map[c]
                    for c in self.product_type_category_priority
                    if c in category_ids
                ),
                None,
            )
        if not product_type:
            matches = {
                self.product_type_category_map[c]
                for c in category_ids
                if c in self.product_type_category_map
            }
            if len(matches) == 1:
                product_type = matches.pop()
            elif len(matches) > 1:
                raise ValueError("Conflicting normalized garment categories")
        if not product_type:
            for rule in self.product_type_title_rules:
                if set(rule.category_ids) & set(category_ids) and re.search(
                    rule.pattern, facts["title"]
                ):
                    product_type = rule.product_type
                    break
        currency = self.currency_map.get(facts["currency"])
        if not currency:
            raise ValueError(f"Unmapped currency: {facts['currency']}")
        attributes = {key: mapping.extract(element) for key, mapping in self.attributes.items()}
        attributes = {key: value for key, value in attributes.items() if value is not None}
        images = list(
            dict.fromkeys(
                item for path in self.image_paths for item in values(element, path) if item
            )
        )
        product = ProductGroup(
            id=product_id,
            external_id=external,
            source_model_id=facts.get("source_model_id") or facts["visual_id"],
            title=facts["title"],
            description=facts.get("description"),
            brand=facts.get("brand"),
            product_type=product_type,
            source_categories=[known_categories[c] for c in category_ids],
            color=color,
            catalog_attributes=attributes,
            product_url=facts.get("product_url"),
            image_urls=images,
        )
        raw = {"@" + key: value for key, value in element.attrib.items()}
        for child in element:
            entry = {"text": "".join(child.itertext()).strip(), **child.attrib}
            raw.setdefault(child.tag, []).append(entry)
        offer = Offer(
            id=stable_id("offer", facts["offer_id"]),
            external_id=facts["offer_id"],
            product_group_id=product_id,
            sku=facts.get("sku"),
            barcode=facts.get("barcode"),
            size=facts.get("size"),
            price=Decimal(facts["price"]),
            old_price=Decimal(facts["old_price"]) if facts.get("old_price") else None,
            currency=currency,
            available=self.availability.evaluate(element),
            source_attributes=raw,
        )
        return CatalogProduct(product=product, offers=[offer])
