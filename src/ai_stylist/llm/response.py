from ai_stylist.llm.intent import usage_dict
from ai_stylist.schemas.styling import ResponseRequest, StylingResponse, VLMStylingResponse


def validate_rewrite(original: VLMStylingResponse, rewritten: StylingResponse) -> None:
    def assignments(looks):
        return [
            (
                look.plan_id,
                sorted((i.slot, i.product_id, i.offer_id) for i in look.items),
                look.score.model_dump(),
            )
            for look in looks
        ]

    if assignments(original.looks) != assignments(rewritten.looks):
        raise ValueError("Response model changed validated assignments or scores")
    if original.limitations != rewritten.limitations:
        raise ValueError("Response model changed grounded limitations")


class ResponseService:
    def __init__(self, agent):
        self.agent = agent
        self.last_usage = {}

    async def respond(self, request: ResponseRequest) -> StylingResponse:
        result = await self.agent.run(request.model_dump_json(), deps={})
        self.last_usage = usage_dict(result)
        validate_rewrite(request.styling_result, result.output)
        return result.output
