"""In-memory implementation of `ConversationMemoryPort` (ADR-014, Opción B).

Buffer efímero por sala: ``token -> deque`` acotado por `max_messages` (eviction
FIFO del turno más antiguo) y con TTL por entrada (`ttl_seconds`). No persiste:
vive en la RAM del proceso y se pierde al reiniciar (comportamiento aceptado).

CONSTRAINT — 1 worker
---------------------
El buffer NO se comparte entre procesos/workers: cada worker tendría su propio
diccionario. El despliegue debe correr con **un único worker** para que una sala
acumule contexto coherente. Escalar a múltiples réplicas exigiría un store
compartido (Opción C de ADR-014); queda registrado como deuda D7, no se resuelve
aquí.

Concurrencia
------------
Seguro para un **único event-loop asyncio**: `record`/`history` son síncronos y
no ceden el control (sin `await`), así que se ejecutan de forma atómica respecto
de otras corrutinas del mismo loop. No necesita locks (no hay paralelismo de
hilos en la ruta de petición).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from app.domain.message import Message, Role

_ALLOWED_ROLES: frozenset[str] = frozenset({"user", "assistant"})


@dataclass(frozen=True)
class _Entry:
    role: Role
    author: str
    text: str
    at: float  # marca de tiempo monotónica del registro (para el TTL)


class InMemoryConversationMemory:
    """Buffer in-memory por sala con cota de tamaño y TTL.

    `clock` es inyectable (default `time.monotonic`) para poder probar la
    expiración por TTL de forma determinista, sin dormir el test.
    """

    def __init__(
        self,
        *,
        max_messages: int,
        ttl_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_messages = max_messages
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._rooms: dict[str, deque[_Entry]] = {}

    def record(self, token: str, role: str, author: str, text: str) -> None:
        if role not in _ALLOWED_ROLES:
            raise ValueError(
                f"Invalid memory role {role!r}; expected one of {sorted(_ALLOWED_ROLES)}."
            )
        room = self._rooms.get(token)
        if room is None:
            # maxlen aplica la eviction FIFO del turno más antiguo sin código extra.
            room = deque(maxlen=self._max_messages)
            self._rooms[token] = room
        self._prune(room)
        room.append(_Entry(role=role, author=author, text=text, at=self._clock()))

    def history(self, token: str) -> list[Message]:
        room = self._rooms.get(token)
        if room is None:
            return []
        self._prune(room)
        # `author` se retiene en el buffer pero no se inyecta en el Message: la
        # historia se reproduce como turnos role/text. Atribución por autor en
        # salas multi-usuario es trabajo futuro (YAGNI hoy).
        return [Message(role=entry.role, content=entry.text) for entry in room]

    def _prune(self, room: deque[_Entry]) -> None:
        """Descarta del frente los turnos expirados por TTL.

        Las entradas están en orden cronológico (append a la derecha), así que en
        cuanto encontramos una vigente, todas las posteriores también lo son.
        """
        cutoff = self._clock() - self._ttl_seconds
        while room and room[0].at < cutoff:
            room.popleft()
