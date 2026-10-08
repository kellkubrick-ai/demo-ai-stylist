from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, StrictUndefined


class PromptRenderer:
    def __init__(self, knowledge_dir: Path):
        self.knowledge_dir = knowledge_dir
        self.environment = Environment(undefined=StrictUndefined, autoescape=False)

    def knowledge(self) -> dict[str, str]:
        return {
            "stylist_rules": (self.knowledge_dir / "stylist_rules.md").read_text(encoding="utf-8"),
            "compatibility_rubric": (self.knowledge_dir / "compatibility_rubric.md").read_text(
                encoding="utf-8"
            ),
        }

    def render(self, stage: str, context: dict) -> str:
        source = files("ai_stylist").joinpath("prompts", f"{stage}.md").read_text(encoding="utf-8")
        return self.environment.from_string(source).render(**context)
