"""Framework-free Output guardrail stages and stable rejection reasons."""

from enum import StrEnum


class OutputGuardrailStage(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    COMPLETE = "complete"


class OutputGuardrailOutcome(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class OutputGuardrailErrorCode(StrEnum):
    INPUT_BYTES_EXCEEDED = "output_input_bytes_exceeded"
    EMPTY_OUTPUT = "output_empty"
