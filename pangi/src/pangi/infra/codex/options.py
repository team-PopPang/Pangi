from __future__ import annotations


def append_model_reasoning_effort(command: list[str], reasoning_effort: str | None) -> None:
    if reasoning_effort:
        command.extend(("-c", f'model_reasoning_effort="{reasoning_effort}"'))
