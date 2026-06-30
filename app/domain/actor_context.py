"""The resolved identity a skill executes under (ADR-016 / ADR-018). Pure data.

A skill never parses the raw Talk ``actor_id``; it receives the identity already
resolved in this value object. The engine builds it once per request and passes
it to every :meth:`~app.services.skill.Skill.execute` call, so *which identity a
skill used* is an explicit parameter, not global state (refuerza ADR-003 y hace
auditable la identidad).

BLOQUE 1 (este código): la identidad real impersonada está **bloqueada por un
spike pendiente** (ADR-016). Por eso:

* ``role_scope`` es **fijo** ``"corporate"`` (consistente con ADR-011, scope fijo
  en la ruta de petición).
* ``impersonated_uid`` está **presente pero es ``None``**: el campo existe para no
  romper el contrato cuando el Bloque 2 lo rellene con el uid resuelto; hoy nadie
  lo consume y las skills read-only operan *app-only* (sin impersonation).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActorContext:
    """Identidad resuelta del invocador, transportada hacia la skill.

    * ``actor_id``         — id crudo de Talk del invocador (p. ej.
      ``"users/alice"``). Se conserva para atribución/diagnóstico; las skills
      **no** lo parsean para derivar permisos.
    * ``token``            — token de la sala de Talk desde la que se invoca.
    * ``role_scope``       — alcance de rol para filtrar datos. **Fijo**
      ``"corporate"`` en el Bloque 1.
    * ``impersonated_uid`` — uid del usuario impersonado (ADR-016). **``None``**
      en el Bloque 1; lo rellena el Bloque 2 tras cerrar el spike de identidad.
    """

    actor_id: str
    token: str
    role_scope: str = "corporate"
    impersonated_uid: str | None = None
