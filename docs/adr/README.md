# ADRs — GCF Talk AI Bot

Las decisiones de arquitectura de la **Fase 1** (mención→LLM) y la **Fase 2**
(RAG pgvector) viven inline en [`ARCHITECTURE.md §5`](../../ARCHITECTURE.md)
como **ADR-001 … ADR-014** (formato tabla: Problema / Decisión / Consecuencias /
Alternativas descartadas).

Este directorio recoge los ADRs **standalone** de la siguiente iteración —
**motor de agente por _tool-calling_ y skills** (sucesora de la Fase 2 RAG).
Mantienen el mismo esquema de cuatro secciones.

| ADR | Título | Estado |
|-----|--------|--------|
| ADR-015 | _(sin asignar — hueco de numeración; reservado)_ | — |
| [ADR-016](./ADR-016-modelo-identidad-skills.md) | Modelo de identidad para skills: impersonation vía `nc.set_user` | **Propuesto** |
| [ADR-017](./ADR-017-motor-agente-tool-calling.md) | Motor de agente por _tool-calling_ (router = LLM, no regex) | **Propuesto** |
| [ADR-018](./ADR-018-contrato-skill-registry.md) | Contrato de `Skill` + `SkillRegistry` (Strategy + Registry + Command) | **Propuesto** |
| [ADR-019](./ADR-019-external-connector.md) | `ExternalConnectorPort` + un adapter concreto (CRM) | **Propuesto** |

> **Estado `Propuesto`**: estos ADRs aún no se han implementado. Varios
> dependen de un **spike** previo (p. ej. confirmar los _scopes_ AppAPI y la
> migración `manual_install`→HaRP de ADR-016). Los puntos marcados **TBD** se
> cierran con ese spike.
