import json
from decimal import Decimal

from ai_stylist.retriever.query_builder import offer_matches, product_matches
from ai_stylist.schemas.catalog import CatalogProduct
from ai_stylist.schemas.planning import OutfitPlan
from ai_stylist.schemas.retrieval import CandidatePool, MissingRequiredSlot, RetrievalCandidate


def matches(record, intent, slot) -> bool:
    return product_matches(record.product, intent, slot) and any(
        offer_matches(offer, intent, slot) for offer in record.offers
    )


def assign_required(composition, records, intent) -> tuple[dict[str, str], list[str]]:
    """Bipartite matching also prevents one multi-role product covering two required roles."""
    options = {
        slot.role: [r.product.id for r in records if matches(r, intent, slot)]
        for slot in composition.slots
        if slot.required
    }
    product_role = {}

    def assign(role, visited):
        for product_id in options[role]:
            if product_id in visited:
                continue
            visited.add(product_id)
            previous = product_role.get(product_id)
            if previous is None or assign(previous, visited):
                product_role[product_id] = role
                return True
        return False

    for role in sorted(options, key=lambda r: len(options[r])):
        assign(role, set())
    role_product = {role: product_id for product_id, role in product_role.items()}
    return role_product, [role for role in options if role not in role_product]


def missing_slots(plan, records, intent):
    return [
        MissingRequiredSlot(plan_id=composition.plan_id, slot=role)
        for composition in plan.compositions
        for role in assign_required(composition, records, intent)[1]
    ]


def covered_plan(pool: CandidatePool, plan: OutfitPlan, intent) -> OutfitPlan:
    records = [candidate.product for candidate in pool.products]
    return OutfitPlan(
        compositions=[
            composition
            for composition in plan.compositions
            if not assign_required(composition, records, intent)[1]
        ]
    )


def allocate_pool(request, ranked: dict[tuple[str, str], list[tuple[CatalogProduct, float]]]):
    all_records = {}
    scores = {}
    for rows in ranked.values():
        for record, score in rows:
            all_records[record.product.id] = record
            scores[record.product.id] = max(score, scores.get(record.product.id, -float("inf")))
    selected = {}
    viable = []
    for composition in request.outfit_plan.compositions:
        assignment, missing = assign_required(
            composition, list(all_records.values()), request.intent
        )
        if missing:
            continue
        viable.append(composition)
        needed = {pid: all_records[pid] for pid in assignment.values()}
        if len(selected.keys() | needed.keys()) <= request.max_products:
            selected.update(needed)
    # Round robin required slots, then optional slots, retaining reserved complete bases.
    for required in (True, False):
        queues = [
            list(ranked.get((composition.plan_id, slot.role), []))
            for composition in viable
            for slot in composition.slots
            if slot.required == required
        ]
        while any(queues) and len(selected) < request.max_products:
            for queue in queues:
                while queue:
                    record, _ = queue.pop(0)
                    if record.product.id not in selected:
                        selected[record.product.id] = record
                        break
                if len(selected) == request.max_products:
                    break
    candidates = []
    slots = [slot for c in request.outfit_plan.compositions for slot in c.slots]
    for record in selected.values():
        eligible = [slot for slot in slots if matches(record, request.intent, slot)]
        offers = [
            offer
            for offer in record.offers
            if any(offer_matches(offer, request.intent, slot) for slot in eligible)
        ]
        candidates.append(
            RetrievalCandidate(
                product=record.model_copy(update={"offers": offers}),
                eligible_slots=list(dict.fromkeys(slot.role for slot in eligible)),
                retrieval_score=scores[record.product.id],
            )
        )
    return CandidatePool(
        products=candidates,
        missing_required_slots=missing_slots(
            request.outfit_plan, list(selected.values()), request.intent
        ),
    )


class Retriever:
    def __init__(self, repository, embedder):
        self.repository = repository
        self.embedder = embedder
        self.last_usage = {}

    async def retrieve(self, request) -> CandidatePool:
        ranked = {}
        vectors = {}
        usage = []
        for composition in request.outfit_plan.compositions:
            for slot in composition.slots:
                query = json.dumps(
                    {
                        "occasion": request.intent.occasion,
                        "styles": request.intent.styles,
                        "formality": request.intent.formality,
                        "fit": request.intent.fit_preferences,
                        "figure_preferences": request.intent.figure_preferences,
                        "avoid": request.intent.avoid,
                        "role": slot.role,
                        "query": slot.retrieval_query,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if query not in vectors:
                    vectors[query] = await self.embedder.embed(query)
                    usage.append(dict(getattr(self.embedder, "last_usage", {})))
                ranked[composition.plan_id, slot.role] = await self.repository.rank_slot(
                    request.intent,
                    slot,
                    vectors[query],
                    self.embedder.model,
                    limit=30,
                )
        costs = [row.get("cost") for row in usage]
        self.last_usage = {
            "requests": len(usage),
            "input_tokens": sum(row.get("input_tokens", 0) for row in usage),
            "cost": str(sum((Decimal(cost) for cost in costs), Decimal("0")))
            if costs and all(cost is not None for cost in costs)
            else None,
            "model_id": self.embedder.model,
        }
        return allocate_pool(request, ranked)
