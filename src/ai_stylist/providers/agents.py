import re

from openai import AsyncOpenAI
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from ai_stylist.config import Settings
from ai_stylist.providers.prompts import PromptRenderer
from ai_stylist.schemas.enrichment import ProductEnrichment

LATIN_TEXT = re.compile(r"[A-Za-z]")
VISUAL_WEIGHTS = {"лёгкий", "средний", "тяжёлый"}
ENRICHMENT_LIST_LIMITS = {
    "styles": 4,
    "occasions": 4,
    "color_characteristics": 4,
    "layering_suitability": 3,
}


def enrichment_latin_fields(output: ProductEnrichment) -> list[str]:
    fields = []
    for name, value in output.model_dump().items():
        values = value if isinstance(value, list) else [value]
        if any(isinstance(item, str) and LATIN_TEXT.search(item) for item in values):
            fields.append(name)
    return fields


def enrichment_contract_issues(output: ProductEnrichment) -> list[str]:
    issues = []
    for field, limit in ENRICHMENT_LIST_LIMITS.items():
        if len(getattr(output, field)) > limit:
            issues.append(f"{field} must contain at most {limit} values")
    if output.visual_weight is not None and output.visual_weight not in VISUAL_WEIGHTS:
        issues.append("visual_weight must be лёгкий, средний, тяжёлый, or null")
    return issues


def stage_model_settings(settings: Settings, stage: str) -> dict:
    effort = getattr(settings, f"{stage}_reasoning_effort")
    return {
        "openrouter_usage": {"include": True},
        "openrouter_reasoning": {"effort": effort, "exclude": True},
    }


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
        model_settings=stage_model_settings(settings, stage),
    )

    @agent.instructions
    def instructions(ctx: RunContext[dict]) -> str:
        return renderer.render(stage, ctx.deps)

    if stage == "enrichment":

        @agent.output_validator
        def require_russian_values(output):
            fields = enrichment_latin_fields(output)
            issues = enrichment_contract_issues(output)
            if fields:
                issues.append(
                    "all textual values must use Russian Cyrillic; translate values in: "
                    + ", ".join(fields)
                )
            if issues:
                raise ModelRetry("Correct ProductEnrichment: " + "; ".join(issues) + ".")
            return output

    return agent
