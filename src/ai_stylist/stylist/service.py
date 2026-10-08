from ai_stylist.llm.intent import usage_dict
from ai_stylist.retriever.query_builder import offer_matches, product_matches
from ai_stylist.retriever.service import covered_plan
from ai_stylist.schemas.styling import VLMOfferCandidate, VLMProductCandidate, VLMStylingRequest


def build_styling_request(intent, plan, pool, knowledge) -> VLMStylingRequest:
    viable = covered_plan(pool, plan, intent)
    if not viable.compositions:
        raise ValueError("No composition has complete required-role coverage")
    slots = [slot for composition in viable.compositions for slot in composition.slots]
    products = []
    for candidate in pool.products:
        record = candidate.product
        group = record.product
        eligible = [slot for slot in slots if product_matches(group, intent, slot)]
        offers = []
        for offer in record.offers:
            roles = list(
                dict.fromkeys(slot.role for slot in eligible if offer_matches(offer, intent, slot))
            )
            if roles:
                offers.append(
                    VLMOfferCandidate(
                        offer_id=offer.id,
                        eligible_slots=roles,
                        size=offer.size,
                        price=offer.price,
                        currency=offer.currency,
                    )
                )
        if not offers:
            continue
        roles = list(dict.fromkeys(role for offer in offers for role in offer.eligible_slots))
        products.append(
            VLMProductCandidate(
                product_id=group.id,
                eligible_slots=roles,
                title=group.title,
                product_type=group.product_type,
                source_category_names=[c.name for c in group.source_categories],
                color=group.color,
                brand=group.brand,
                original_description=group.description,
                catalog_attributes=group.catalog_attributes,
                inferred_styling_attributes=record.enrichment,
                eligible_offers=offers,
                image_urls=[str(url) for url in group.image_urls[:2]],
            )
        )
    return VLMStylingRequest(
        intent=intent,
        outfit_plan=viable,
        products=products,
        missing_required_slots=pool.missing_required_slots,
        **knowledge,
    )


class StylistService:
    def __init__(self, agent, images):
        self.agent = agent
        self.images = images
        self.last_usage = {}

    async def style(self, request):
        content = [request.model_dump_json(), *await self.images.attach(request.products)]
        result = await self.agent.run(
            content,
            deps={
                "stylist_rules": request.stylist_rules,
                "compatibility_rubric": request.compatibility_rubric,
            },
        )
        self.last_usage = usage_dict(result)
        return result.output
