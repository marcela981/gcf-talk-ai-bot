"""Port (interface) for the per-room conversational memory buffer.

ADR-014 (Opción B): la memoria conversacional es un *buffer in-memory por sala*,
acotado y efímero. El bot ya recibe por webhook todos los mensajes de las salas
donde está instalado; este puerto define cómo se registran esos turnos y cómo se
reproducen como contexto previo al construir el prompt.

El contrato es deliberadamente síncrono: la implementación vigente vive en RAM y
no hace I/O (sin red, sin BD). Mantenerlo síncrono evita teñir de `async` a un
componente que nunca espera. Implementaciones en `app/adapters/`.
"""
from __future__ import annotations

from typing import Protocol

from app.domain.message import Message


class ConversationMemoryPort(Protocol):
    def record(self, token: str, role: str, author: str, text: str) -> None:
        """Registra un turno en la sala `token`.

        `role` ∈ {``user``, ``assistant``}. `author` identifica a quien emite el
        turno (``actor_id`` de Talk para humanos, el nombre del bot para sus
        propias respuestas); se retiene para atribución/diagnóstico aunque la
        historia reproducida hoy solo use `role`/`text` (ver adapter).
        """
        ...

    def history(self, token: str) -> list[Message]:
        """Devuelve los turnos previos de la sala `token` en orden cronológico.

        Solo turnos `user`/`assistant`; nunca incluye L0 ni el mensaje actual.
        Aislamiento estricto por `token`: una sala jamás ve el contexto de otra.
        """
        ...
