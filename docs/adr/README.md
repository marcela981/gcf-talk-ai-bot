# ADRs — GCF Talk AI Bot

Las decisiones de arquitectura de la **Fase 1** (mención→LLM) y la **Fase 2**
(RAG pgvector) viven inline en [`ARCHITECTURE.md §5`](../../ARCHITECTURE.md)
como **ADR-001 … ADR-014** (formato tabla: Problema / Decisión / Consecuencias /
Alternativas descartadas).

Este directorio recoge los ADRs **standalone** de dos iteraciones posteriores a
la Fase 2 RAG:

- **ADR-016 … ADR-019** — **motor de agente por _tool-calling_ y skills**.
- **ADR-020 … ADR-023** — **integración read-only con la BD del dashboard
  corporativo** (`dashboard_db`, MySQL en VPS3 — **Bloque 3**).

Todos mantienen el mismo esquema de cuatro secciones (Problema / Decisión /
Consecuencias / Alternativas descartadas).

| ADR | Título | Estado |
|-----|--------|--------|
| ADR-015 | _(sin asignar — hueco de numeración; reservado)_ | — |
| [ADR-016](./ADR-016-modelo-identidad-skills.md) | Modelo de identidad para skills: impersonation vía `nc.set_user` | **Propuesto** |
| [ADR-017](./ADR-017-motor-agente-tool-calling.md) | Motor de agente por _tool-calling_ (router = LLM, no regex) | **Propuesto** |
| [ADR-018](./ADR-018-contrato-skill-registry.md) | Contrato de `Skill` + `SkillRegistry` (Strategy + Registry + Command) | **Propuesto** |
| [ADR-019](./ADR-019-external-connector.md) | `ExternalConnectorPort` + un adapter concreto (CRM) | **Propuesto** |
| [ADR-020](./ADR-020-dashboard-port.md) | `DashboardPort`: fuente de contexto estructurado read-only (dashboard_db) | **Propuesto** |
| [ADR-021](./ADR-021-identidad-en-bd.md) | Identidad en la BD: la regla de oro (filtro obligatorio por usuario) | **Propuesto** |
| [ADR-022](./ADR-022-gate-infra-credenciales.md) | Gate de infra + credenciales read-only para `dashboard_db` | **Propuesto** |
| [ADR-023](./ADR-023-fuente-autoritativa.md) | Estrategia de fuente autoritativa: dashboard_db vs Nextcloud en vivo | **Propuesto** |

> **Estado `Propuesto`**: estos ADRs aún no se han implementado. Varios
> dependen de un **spike** o de un **prerequisito de infra** previo — p. ej.
> confirmar los _scopes_ AppAPI y la migración `manual_install`→HaRP de ADR-016,
> o **abrir la ruta de red VPS2→VPS3** de ADR-022. Los puntos marcados **TBD** se
> cierran con ese trabajo previo.

> **Deudas nuevas propuestas por el Bloque 3:** **D9** (acoplamiento al esquema de
> `dashboard_db`, cross-repo — ADR-020) y **D10** (dependencia operativa de VPS3 +
> rotación de credenciales — ADR-022).
