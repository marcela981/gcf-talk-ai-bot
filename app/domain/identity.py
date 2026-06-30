"""Resolución de identidad impersonable desde el `actor_id` de Talk (ADR-016).

Lógica pura (stdlib): mapea el `actor_id` crudo que entrega Talk al `uid` local
impersonable que transporta :class:`~app.domain.actor_context.ActorContext`. Solo
``users/<uid>`` es impersonable; invitados y federados no tienen `uid` local y
devuelven ``None`` (una skill con efectos se rehúsa, ADR-016). ``bots/…`` ya se
filtró antes del agente (anti-loop, ADR-014); por defensa también devuelve ``None``.
"""
from __future__ import annotations

_USERS_PREFIX = "users/"


def resolve_impersonated_uid(actor_id: str) -> str | None:
    """`actor_id` de Talk → `uid` local impersonable, o ``None`` si no hay identidad.

    * ``users/<uid>``           → ``<uid>`` (impersonable).
    * ``guests/…``, ``bridged/…``, ``federated_users/…``, ``bots/…``, vacío → ``None``.

    El `uid` se conserva literal tras el prefijo ``users/`` (no se parsea más): es
    el identificador local que el adapter usará en ``set_user``/auth impersonada.
    """
    if not actor_id:
        return None
    if actor_id.startswith(_USERS_PREFIX):
        uid = actor_id[len(_USERS_PREFIX) :].strip()
        return uid or None
    return None
