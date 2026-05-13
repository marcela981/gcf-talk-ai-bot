"""Domain entity for a single chat message exchanged with the LLM."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant"]

_ALLOWED_ROLES: frozenset[str] = frozenset({"system", "user", "assistant"})


@dataclass(frozen=True)
class Message:
    role: Role
    content: str

    def __post_init__(self) -> None:
        if self.role not in _ALLOWED_ROLES:
            raise ValueError(
                f"Invalid role {self.role!r}; expected one of {sorted(_ALLOWED_ROLES)}."
            )
