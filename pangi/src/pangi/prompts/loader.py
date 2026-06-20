from __future__ import annotations

from functools import lru_cache
from importlib import resources


PROMPT_PACKAGE = "pangi.prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    prompt = resources.files(PROMPT_PACKAGE).joinpath(name)
    return prompt.read_text(encoding="utf-8").strip()
