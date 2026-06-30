"""The resolved identity a skill executes under (ADR-016 / ADR-018). Pure data.

A skill never parses the raw Talk ``actor_id``; it receives the identity already
resolved in this value object. The engine builds it once per request and passes
it to every :meth:`~app.services.skill.Skill.execute` call, so *which identity a
skill used* is an explicit parameter, not global state (refuerza ADR-003 y hace
auditable la identidad).

Identidad (ADR-016, spike cerrado — `docs/spikes/SPIKE_IMPERSONATION.md`):

* ``role_scope`` es **fijo** ``"corporate"`` (consistente con ADR-011, scope fijo
  en la ruta de petición; la derivación por grupos del usuario es trabajo futuro).
* ``impersonated_uid`` se **resuelve por request** desde el ``actor_id`` de Talk
  (``app.domain.identity.resolve_impersonated_uid``): ``users/<uid>`` → ``<uid>``;
  invitados/federados → ``None``. Una skill **con efectos/identidad** se rehúsa
  cuando es ``None``; las skills *app-only* (p. ej. base de conocimiento) lo ignoran.
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
    * ``impersonated_uid`` — uid del usuario impersonado (ADR-016), o ``None`` si el
      invocador no tiene identidad local (invitado/federado). Resuelto por request.
    """

    actor_id: str
    token: str
    role_scope: str = "corporate"
    impersonated_uid: str | None = None
