# ADR-019 — `ExternalConnectorPort` + un adapter concreto (CRM)

| | |
|--|--|
| **Estado** | **Propuesto** (2026-06-29) |
| **Iteración** | Motor de agente / skills (sucesora de la Fase 2 RAG) |
| **Relacionados** | ADR-002 (puerto + adapter, mismo patrón que `LLMPort`), ADR-016 (identidad), ADR-018 (contrato de Skill), ARCHITECTURE §3 (regla de capas), D6 (acoplamiento a proveedor externo) |

## Problema

Algunas skills (ADR-018) deben **leer/escribir en sistemas corporativos fuera de
Nextcloud**. El **primer caso real** es un **CRM**. ¿Cómo se integra ese sistema
**sin acoplar** la skill al SDK/HTTP del proveedor y **sin sobre-diseñar** un
framework de integración genérico?

## Decisión

Un puerto **`ExternalConnectorPort`** en `services/` —contrato mínimo de las
operaciones que las skills necesitan del sistema externo— **+ UN adapter
concreto** que lo implementa contra el **CRM real**. La skill depende del
**puerto**, no del proveedor. La **identidad del invocador** (ADR-016) se propaga
al connector para la **autorización aguas abajo**.

### KISS / YAGNI explícito

**No** se construye un **framework de plugins/conectores genérico** (registro
dinámico de targets, descubrimiento, manifests, config declarativa) **mientras
haya UN solo target**. La generalización se **difiere hasta ≥2 sistemas externos
reales**; con dos, el patrón común se **extrae de código que ya funciona**, no de
especulación.

## Consecuencias

- **Misma receta que ADR-002 (`LLMPort`)** aplicada al CRM: el dominio/servicio
  no conoce el SDK del proveedor; cambiarlo o mockearlo es **un adapter**. Tests
  sin red.
- Una **skill de CRM** (ADR-018) se vuelve un adapter **delgado** que orquesta
  `ExternalConnectorPort`; respeta la regla de capas (ARCHITECTURE §3).
- **Se evita deuda de over-engineering**: nada de _plugin host_, manifests ni
  carga dinámica. **Costo asumido**: el **segundo target** obligará a una pequeña
  refactor (extraer lo común) — aceptado a sabiendas (refactor barato sobre 2
  ejemplos reales **>** abstracción prematura sobre 1).
- **Acoplamiento a un proveedor externo más** (análogo a **D6** con Supabase):
  registrar como **deuda nueva** (propuesta **D8**) el acoplamiento al CRM
  (dominio, esquema, auth y disponibilidad fuera de nuestro control).

## Alternativas descartadas

- **Framework de conectores genérico desde el día 1** (interfaz de plugin +
  registry de targets + config declarativa): **YAGNI** con un único target;
  abstracción especulativa que suele **adivinar mal** los ejes de variación. Se
  **reabre con ≥2 targets**.
- **Skill que habla directo al SDK/HTTP del CRM**: acopla la lógica al proveedor,
  no es testeable sin red y repite el antipatrón que **ADR-002** ya descartó para
  OpenAI.
- **Reusar un puerto existente** forzando el CRM dentro de `LLMPort`/otros: viola
  **SRP**; el CRM no es un LLM ni un store vectorial.

## Scopes AppAPI (TBD — confirmar por spike)

El connector es **saliente hacia un sistema NO-Nextcloud**; en principio **no**
requiere _scopes_ AppAPI adicionales más allá de los de ADR-016. **TBD**:
confirmar por spike si la resolución de identidad del invocador (ADR-016) exige
algún _scope_ extra.
