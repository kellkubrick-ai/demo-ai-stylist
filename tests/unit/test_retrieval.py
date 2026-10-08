from ai_stylist.retriever.query_builder import offer_matches, product_matches
from ai_stylist.retriever.service import allocate_pool, assign_required, covered_plan
from ai_stylist.schemas.intent import StylingBudget, StylingIntent
from ai_stylist.schemas.planning import OutfitComposition, OutfitPlan, OutfitSlot
from ai_stylist.schemas.retrieval import RetrievalRequest


def test_clothing_shoes_and_bag_sizes_are_separate(records):
    intent = StylingIntent(sizes_by_role={"top": ["M"], "shoes": ["38"]})
    shoes = next(r for r in records if r.product.product_type == "shoes")
    slot = OutfitSlot(role="shoes", retrieval_query="shoes")
    assert [o.size for o in shoes.offers if offer_matches(o, intent, slot)] == ["38"]
    bag = next(r for r in records if r.product.product_type == "bag")
    assert offer_matches(bag.offers[0], intent, OutfitSlot(role="bag", retrieval_query="bag"))


def test_budget_and_current_price_are_authoritative(records):
    top = next(r for r in records if r.product.product_type == "blouse")
    slot = OutfitSlot(role="top", retrieval_query="top")
    assert offer_matches(
        top.offers[0], StylingIntent(budget=StylingBudget(max_item_price="150")), slot
    )
    assert not offer_matches(
        top.offers[0], StylingIntent(budget=StylingBudget(currency="USD")), slot
    )


def test_catalog_color_alias_and_bundle_confirmation(records):
    bottom = next(r for r in records if r.product.product_type == "trousers")
    assert product_matches(
        bottom.product,
        StylingIntent(colors=["Чёрный"]),
        OutfitSlot(role="bottom", retrieval_query="bottom"),
    )
    record = next(r for r in records if r.product.product_type == "suit_set")
    slot = OutfitSlot(role="set", retrieval_query="set")
    assert product_matches(record.product, StylingIntent(), slot)
    record.product.catalog_attributes = {}
    assert not product_matches(record.product, StylingIntent(), slot)


def test_pool_limit_preserves_base_before_optional_candidates(records, plan):
    intent = StylingIntent()
    ranked = {
        ("separates", slot.role): [
            (r, 1.0) for r in records if product_matches(r.product, intent, slot)
        ]
        for slot in plan.compositions[0].slots
    }
    pool = allocate_pool(RetrievalRequest(intent=intent, outfit_plan=plan, max_products=3), ranked)
    assert len(pool.products) == 3
    assert not pool.missing_required_slots
    assert {role for c in pool.products for role in c.eligible_slots} >= {"top", "bottom", "shoes"}


def test_missing_required_and_optional_roles_differ(records, plan):
    intent = StylingIntent()
    ranked = {("separates", "top"): [(records[0], 1.0)]}
    pool = allocate_pool(RetrievalRequest(intent=intent, outfit_plan=plan), ranked)
    assert not covered_plan(pool, plan, intent).compositions
    assert {m.slot for m in pool.missing_required_slots} >= {"bottom", "shoes"}
    assert "bag" not in {m.slot for m in pool.missing_required_slots}


def test_one_product_cannot_cover_two_required_roles(records):
    cardigan = next(r for r in records if r.product.product_type == "cardigan")
    composition = OutfitComposition(
        plan_id="layer",
        slots=[
            OutfitSlot(
                role="top", retrieval_query="cardigan", product_type_constraints=["cardigan"]
            ),
            OutfitSlot(role="bottom", retrieval_query="trousers"),
            OutfitSlot(role="shoes", retrieval_query="shoes"),
            OutfitSlot(
                role="outer_layer",
                retrieval_query="cardigan",
                product_type_constraints=["cardigan"],
            ),
        ],
    )
    selected = [cardigan, *[r for r in records if r.product.product_type in ("trousers", "shoes")]]
    assignment, missing = assign_required(composition, selected, StylingIntent())
    assert len(assignment) == 3
    assert missing


def test_viable_alternative_survives_missing_other_plan(records, plan):
    dress_plan = OutfitComposition(
        plan_id="dress",
        slots=[
            OutfitSlot(role="dress", retrieval_query="dress"),
            OutfitSlot(role="shoes", retrieval_query="shoes"),
        ],
    )
    combined = OutfitPlan(compositions=[*plan.compositions, dress_plan])
    request = RetrievalRequest(intent=StylingIntent(), outfit_plan=combined)
    dress = next(r for r in records if r.product.product_type == "dress")
    shoes = next(r for r in records if r.product.product_type == "shoes")
    pool = allocate_pool(
        request, {("dress", "dress"): [(dress, 1)], ("dress", "shoes"): [(shoes, 1)]}
    )
    assert [c.plan_id for c in covered_plan(pool, combined, request.intent).compositions] == [
        "dress"
    ]
