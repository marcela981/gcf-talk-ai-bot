# ADR-018 — Contrato de `Skill` + `SkillRegistry` (Strategy + Registry + Command)

| | |
|--|--|
| **Estado** | **Propuesto** (2026-06-29) |
| **Iteración** | Motor de agente / skills (sucesora de la Fase 2 RAG) |
| **Relacionados** | ADR-002 (puertos + adapters), ADR-003 (stateless), ADR-016 (identidad), ADR-017 (tool-calling), ADR-019 (External Connector), ARCHITECTURE §3 (regla de capas) |

## Problema

El motor de ADR-017 necesita un conjunto **extensible** de capacidades. ¿Cómo se
define una "skill" para que **(a)** el LLM la vea como _tool_, **(b)** sea
ejecutable con **argumentos validados**, **(c)** se respete la **identidad** del
invocador (ADR-016), y **(d)** **agregar una skill no obligue a tocar** el router
ni el servicio (**OCP**)?

## Decisión

Una interfaz **`Skill`** (Protocol/ABC en `services/`, **sin dependencias de
framework**) con —descrita en prosa, no implementada aquí— estos miembros:

- `name: str` — identificador único; **es el nombre de la _tool_** ante el LLM.
- `description: str` — prosa que el LLM usa para **decidir cuándo** invocarla.
- `parameters_schema` — **JSON-schema** de los argumentos (lo que ADR-017 entrega
  al modelo y contra lo que se validan los argumentos emitidos).
- `execute(args, actor: ActorContext) -> SkillResult` — ejecuta la acción.

Tipos de soporte:

- **`ActorContext`** transporta la **identidad ya resuelta** (uid impersonado,
  token de sala, `role_scope`) de ADR-016 hacia la skill. La skill **no** parsea
  el `actor_id` crudo: lo recibe resuelto.
- **`SkillResult`** es un valor de dominio (éxito / datos / mensaje de error) que
  el loop de ADR-017 convierte en **turno de herramienta** para el LLM.

Un **`SkillRegistry`** mantiene el catálogo `name -> Skill`, **expone los
JSON-schemas** para ADR-017 y **resuelve la skill por nombre** al ejecutar.

### Patrones

- **Strategy** — cada skill es una estrategia intercambiable tras una interfaz
  común.
- **Registry** — catálogo central; descubrimiento y resolución **por nombre**.
- **Command** — cada invocación ≡ `args` + `execute`, encapsula la acción y su
  resultado (`SkillResult`).

### OCP y regla de capas

Alta de una skill = **nueva clase** que implementa `Skill` **+ registro** en el
`SkillRegistry` (en el _composition root_, `app/main.py`). **No** se modifica el
motor (ADR-017), el `LLMPort`, ni `ConversationService`.

La **interfaz** vive en `services/` (puerto/contrato). Las skills concretas que
toquen infraestructura (Nextcloud, CRM) son **adapters** — respetan la regla de
capas de ARCHITECTURE §3: `domain` puro, `services` define contratos, `adapters`
implementa I/O.

## Consecuencias

- **Catálogo testeable en aislamiento**: cada skill con dobles; el `Registry` con
  un _fake_. El LLM-as-router (ADR-017) se prueba con un registry de skills
  falsas, sin red.
- **Identidad como parámetro explícito** de `execute`, no estado global:
  refuerza el stateless (ADR-003) y hace **auditable** qué identidad usó cada
  skill.
- **Disciplina de capas**: una skill de infraestructura **no** puede vivir en
  `domain`; debe ser adapter. **Riesgo**: skills "gordas" que mezclan
  orquestación e I/O — se mitiga manteniendo `execute` **delgado** y delegando el
  I/O a puertos (p. ej. `ExternalConnectorPort`, ADR-019).
- **El `parameters_schema` es contrato público**: cambiarlo altera cómo el LLM
  invoca la skill ⇒ exige **análisis de impacto** al modificarlo (consistente con
  la política de impacto del proyecto).

## Alternativas descartadas

- **Funciones sueltas en un `dict` `name -> callable`**: pierde el contrato
  tipado (`description` / `schema` / identidad), dificulta validar argumentos y
  testear, y degenera en duplicación. El Registry sobre una interfaz formaliza lo
  mismo **sin** esa entropía.
- **`if/elif` por intención dentro del servicio**: viola **OCP** (cada skill
  nueva toca el servicio) — justo lo que este ADR evita.
- **Schema implícito por reflexión** de la firma de `execute`: mágico y frágil;
  preferimos `parameters_schema` **explícito** (es lo que el LLM consume;
  explícito > implícito).
