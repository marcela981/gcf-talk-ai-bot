"""Skill #1 (read-only): `consultar_base_conocimiento` (ADR-018).

Adapter delgado que envuelve el pipeline RAG que YA existe (`EmbedderPort` +
`RetrievalPort` + `RetrievalPolicy`) y lo expone como una *tool* del agente. Es la
prueba end-to-end del tool-use loop sin tocar identidad:

* **READ-ONLY**: solo embebe la consulta y hace `search` (SELECT); no muta nada.
* **`role_scope` fijo `corporate`, app-only**: NO usa `actor.impersonated_uid` ni
  deriva el scope del invocador (a diferencia de la skill de calendario, que sí
  impersona). El scope real por usuario es trabajo futuro (ADR-011, hoy fijo).
  Recibe `ActorContext` por contrato, pero esta skill concreta no necesita la
  identidad.

Regla de capas (§3): toca infraestructura (vector store, embedder) ⇒ es un
**adapter**, no dominio. Mantiene `execute` delgado y delega el I/O a los puertos.
"""
from __future__ import annotations

import logging
from typing import Any

from app.domain.actor_context import ActorContext
from app.domain.retrieval_policy import RetrievalPolicy
from app.domain.skill_result import SkillResult
from app.services.embedder_port import EmbedderPort
from app.services.retrieval_port import RetrievalPort

logger = logging.getLogger(__name__)

_NAME = "consultar_base_conocimiento"
_DESCRIPTION = (
    "Busca información en la base de conocimiento corporativa de GCF (políticas, "
    "procedimientos, documentos internos) por similitud semántica. Úsala cuando el "
    "usuario pregunte por datos, normas o información que pueda estar documentada "
    "internamente y que no debas inventar. Devuelve fragmentos con su fuente para "
    "que puedas citarla."
)
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "consulta": {
            "type": "string",
            "description": (
                "La pregunta o los términos a buscar en la base de conocimiento, "
                "en lenguaje natural."
            ),
        }
    },
    "required": ["consulta"],
    "additionalProperties": False,
}


class KnowledgeBaseSkill:
    """Implementa el contrato `Skill` envolviendo el pipeline RAG de recuperación."""

    def __init__(
        self,
        *,
        embedder: EmbedderPort,
        retrieval: RetrievalPort,
        retrieval_policy: RetrievalPolicy,
        role_scope: str = "corporate",
    ) -> None:
        self._embedder = embedder
        self._retrieval = retrieval
        self._retrieval_policy = retrieval_policy
        self._role_scope = role_scope

    @property
    def name(self) -> str:
        return _NAME

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return _PARAMETERS_SCHEMA

    async def execute(self, args: dict[str, Any], actor: ActorContext) -> SkillResult:
        """Embebe la consulta, busca por similitud y devuelve fragmentos citables.

        `actor` se recibe por contrato pero NO se usa: esta skill es app-only con
        `role_scope` fijo (Bloque 1). El fallo de I/O NO se propaga como excepción
        descontrolada al loop — el loop ya lo atraparía, pero devolver un
        `SkillResult.failure` legible le da mejor señal al modelo.
        """
        query = str(args.get("consulta") or "").strip()
        if not query:
            return SkillResult.failure(
                "Falta el argumento 'consulta' (texto a buscar)."
            )

        try:
            (query_embedding,) = await self._embedder.embed([query])
            chunks = await self._retrieval.search(query_embedding, self._role_scope)
        except Exception as exc:  # noqa: BLE001 — devolver el fallo como dato
            logger.exception("Búsqueda en base de conocimiento falló.")
            return SkillResult.failure(f"Error consultando la base de conocimiento: {exc}")

        selected = self._retrieval_policy.select(chunks)
        if not selected:
            return SkillResult.success(
                {
                    "fragmentos": [],
                    "mensaje": (
                        "Sin resultados pertinentes en la base de conocimiento "
                        "para esa consulta."
                    ),
                }
            )

        return SkillResult.success(
            {
                "fragmentos": [
                    {
                        "fuente": chunk.source,
                        "contenido": chunk.content,
                        "score": chunk.score,
                    }
                    for chunk in selected
                ]
            }
        )
