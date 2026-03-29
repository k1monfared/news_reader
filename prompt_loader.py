"""Prompt template loader. Reads YAML prompt files and fills variables."""

from __future__ import annotations

from pathlib import Path

import yaml


class PromptTemplate:
    """A loaded prompt template with version tracking."""

    def __init__(self, name: str, version: int, system: str, user_template: str):
        self.name = name
        self.version = version
        self.system = system
        self.user_template = user_template

    def render(self, **kwargs: str) -> tuple[str, str, int]:
        """Fill template variables and return (system, user_message, version)."""
        user_message = self.user_template.format(**kwargs)
        return self.system, user_message, self.version


def load_prompt(name: str, prompts_dir: str = "prompts") -> PromptTemplate:
    """Load a prompt template by name from the prompts directory.

    Args:
        name: Prompt name (without .yaml extension)
        prompts_dir: Directory containing prompt YAML files

    Returns:
        PromptTemplate ready to render with variables
    """
    path = Path(prompts_dir) / f"{name}.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)

    return PromptTemplate(
        name=name,
        version=data["version"],
        system=data["system"].strip(),
        user_template=data["user_template"].strip(),
    )
