from ai_stylist.retriever.query_builder import offer_matches, outfit_total, product_matches


def validate_styling(request, result, catalog) -> None:
    plans = {plan.plan_id: plan for plan in request.outfit_plan.compositions}
    candidates = {product.product_id: product for product in request.products}
    records = {record.product.id: record for record in catalog}
    seen_looks = set()
    for look in result.looks:
        if look.plan_id not in plans:
            raise ValueError("Unknown plan ID")
        composition = plans[look.plan_id]
        slots = {slot.role: slot for slot in composition.slots}
        used_roles, used_products = set(), set()
        prices, currencies = [], set()
        for item in look.items:
            if item.slot not in slots or item.slot in used_roles:
                raise ValueError("Unknown or repeated look role")
            if item.product_id not in candidates or item.product_id not in records:
                raise ValueError("Product ID is outside the candidate set")
            if item.product_id in used_products:
                raise ValueError("Repeated product within one look")
            candidate = candidates[item.product_id]
            if item.slot not in candidate.eligible_slots:
                raise ValueError("Product is not eligible for selected role")
            supplied_offer = next(
                (o for o in candidate.eligible_offers if o.offer_id == item.offer_id), None
            )
            if supplied_offer is None or item.slot not in supplied_offer.eligible_slots:
                raise ValueError("Offer is outside the supplied eligible product/role offers")
            record = records[item.product_id]
            offer = next((o for o in record.offers if o.id == item.offer_id), None)
            if offer is None or offer.product_group_id != item.product_id:
                raise ValueError("Offer does not belong to the selected catalog product")
            if not product_matches(record.product, request.intent, slots[item.slot]):
                raise ValueError("Product violates authoritative role constraints")
            if not offer_matches(offer, request.intent, slots[item.slot]):
                raise ValueError("Offer violates availability, size, currency or item budget")
            if (offer.price, offer.currency, offer.size) != (
                supplied_offer.price,
                supplied_offer.currency,
                supplied_offer.size,
            ):
                raise ValueError("Catalog offer changed during styling; request must be rerun")
            used_roles.add(item.slot)
            used_products.add(item.product_id)
            prices.append(offer.price)
            currencies.add(offer.currency)
        required = {slot.role for slot in composition.slots if slot.required}
        if not required <= used_roles:
            raise ValueError("Missing required look role")
        if len(currencies) != 1:
            raise ValueError("Cannot sum an outfit across different currencies")
        budget = request.intent.budget
        if budget and budget.max_outfit_price is not None:
            if outfit_total(prices) > budget.max_outfit_price:
                raise ValueError("Complete outfit exceeds budget")
        identity = tuple(sorted((item.slot, item.product_id) for item in look.items))
        if identity in seen_looks:
            raise ValueError("Duplicate looks (changing a size offer is not a new look)")
        seen_looks.add(identity)
