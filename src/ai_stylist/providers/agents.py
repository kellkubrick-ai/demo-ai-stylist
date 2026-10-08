from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from ai_stylist.config import Settings
from ai_stylist.providers.prompts import PromptRenderer


def make_agent(
    settings: Settings, stage: str, output_type, renderer: PromptRenderer, *, model=None
):
    if model is None:
        field = "stylist_model" if stage == "stylist" else f"{stage}_model"
        settings.require("openrouter_api_key", field)
        client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key.get_secret_value(),
            timeout=settings.request_timeout_seconds,
            max_retries=0,
        )
        model = OpenRouterModel(
            getattr(settings, field),
            provider=OpenRouterProvider(openai_client=client),
        )
    agent = Agent(
        model,
        name=stage,
        output_type=output_type,
        deps_type=dict,
        retries=2,
        model_settings={"openrouter_usage": {"include": True}},
    )

    @agent.instructions
    def instructions(ctx: RunContext[dict]) -> str:
        return renderer.render(stage, ctx.deps)

    return agent
