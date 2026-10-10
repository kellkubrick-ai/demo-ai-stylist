from decimal import Decimal

from sqlalchemy import and_, func, not_, select, true

from ai_stylist.catalog import tables as t
from ai_stylist.schemas.catalog import Offer, ProductGroup
from ai_stylist.schemas.intent import StylingIntent
from ai_stylist.schemas.planning import ROLE_TYPES, OutfitSlot


def normalize(value: str) -> str:
    return value.strip().casefold().replace("ё", "е")


def allowed_types(slot: OutfitSlot) -> set[str]:
    return set(slot.product_type_constraints or ROLE_TYPES[slot.role])


def product_matches(product: ProductGroup, intent: StylingIntent, slot: OutfitSlot) -> bool:
    if product.product_type not in allowed_types(slot):
        return False
    if slot.role == "set" and product.catalog_attributes.get("bundle_confirmed") is not True:
        return False
    if intent.colors and normalize(product.color or "") not in {
        normalize(c) for c in intent.colors
    }:
        return False
    audience = intent.user_context.get("audience")
    if audience and normalize(str(product.catalog_attributes.get("audience", ""))) != normalize(
        audience
    ):
        return False
    excluded = {normalize(s) for s in intent.avoid}
    known = {
        normalize(product.product_type or ""),
        *[normalize(s) for s in product.catalog_attributes.get("features", [])],
    }
    return not bool(excluded & known)


def offer_matches(offer: Offer, intent: StylingIntent, slot: OutfitSlot) -> bool:
    if not offer.available:
        return False
    sizes = intent.sizes_by_role.get(slot.role)
    if sizes and offer.size not in sizes:
        return False
    budget = intent.budget
    if not budget:
        return True
    if offer.currency != budget.currency:
        return False
    if budget.min_item_price is not None and offer.price < budget.min_item_price:
        return False
    caps = [c for c in (budget.max_item_price, budget.max_outfit_price) if c is not None]
    return not caps or offer.price <= min(caps)


def sql_normalize(column):
    return func.replace(func.lower(func.trim(column)), "ё", "е")


def slot_filters(intent: StylingIntent, slot: OutfitSlot):
    product = t.product_groups
    offer = t.offers
    conditions = [product.c.product_type.in_(allowed_types(slot))]
    if slot.role == "set":
        conditions.append(product.c.catalog_attributes["bundle_confirmed"].as_boolean() == true())
    if intent.colors:
        conditions.append(sql_normalize(product.c.color).in_([normalize(c) for c in intent.colors]))
    if audience := intent.user_context.get("audience"):
        conditions.append(
            sql_normalize(product.c.catalog_attributes["audience"].as_string())
            == normalize(audience)
        )
    for excluded in intent.avoid:
        conditions.append(sql_normalize(product.c.product_type) != normalize(excluded))
        # Features are verified source facts, not enrichment. Missing features are not proof.
        features = (
            func.jsonb_array_elements_text(product.c.catalog_attributes["features"])
            .table_valued("value")
            .render_derived()
        )
        conditions.append(
            not_(
                select(features.c.value)
                .where(
                    sql_normalize(features.c.value) == normalize(excluded),
                )
                .exists()
            )
        )
    offers = [offer.c.product_group_id == product.c.id, offer.c.available == true()]
    if sizes := intent.sizes_by_role.get(slot.role):
        offers.append(offer.c.size.in_(sizes))
    if budget := intent.budget:
        offers.append(offer.c.currency == budget.currency)
        if budget.min_item_price is not None:
            offers.append(offer.c.price >= budget.min_item_price)
        for cap in (budget.max_item_price, budget.max_outfit_price):
            if cap is not None:
                offers.append(offer.c.price <= cap)
    conditions.append(select(offer.c.id).where(and_(*offers)).exists())
    conditions.append(
        select(t.product_images.c.id)
        .where(
            t.product_images.c.product_group_id == product.c.id,
            t.product_images.c.is_usable == true(),
        )
        .exists()
    )
    return conditions


def outfit_total(prices: list[Decimal]) -> Decimal:
    return sum(prices, Decimal("0"))
