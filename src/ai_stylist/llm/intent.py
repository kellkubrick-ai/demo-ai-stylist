from ai_stylist.schemas.intent import ROLES
from ai_stylist.schemas.planning import PRODUCT_TYPES, ROLE_TYPES


class IntentService:
    def __init__(self, agent, renderer, repository):
        self.agent = agent
        self.renderer = renderer
        self.repository = repository
        self.last_usage = {}

    async def understand(self, dialogue: list[dict]):
        import json

        result = await self.agent.run(
            json.dumps(dialogue, ensure_ascii=False),
            deps={
                **self.renderer.knowledge(),
                "roles": ROLES,
                "role_types": ROLE_TYPES,
                "audiences": await self.repository.audiences(),
            },
        )
        self.last_usage = usage_dict(result)
        if not set(result.output.intent.product_types) <= set(PRODUCT_TYPES):
            raise ValueError("Intent model returned unknown normalized product types")
        return result.output


def usage_dict(result) -> dict:
    usage = result.usage
    return {
        "model_id": result.response.model_name,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "requests": usage.requests,
        "cost": str(usage.cost) if getattr(usage, "cost", None) is not None else None,
    }
