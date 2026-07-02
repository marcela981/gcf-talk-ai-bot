# ADR-020 — `DashboardPort`: fuente de contexto estructurado read-only (dashboard_db)

| | |
|--|--|
| **Estado** | **Propuesto** (2026-07-02) |
| **Iteración** | Integración con la BD del dashboard corporativo (Bloque 3) |
| **Relacionados** | ADR-002 (puerto + adapter, misma receta que `LLMPort`), ADR-006-ter (corpus RAG en la tabla `documents`), ADR-007 (pgvector), ADR-016 (identidad/impersonation), ADR-018 (contrato de `Skill`), ADR-019 (`ExternalConnectorPort`, primo), ADR-021 / ADR-022 / ADR-023 (Bloque 3), ARCHITECTURE §3 (regla de capas), D9 (acoplamiento a esquema cross-repo) |

## Problema

El bot tiene hoy **dos fuentes de contexto**: (1) el **RAG** (texto no estructurado
del corpus `documents` → pgvector, ADR-006-ter/007) y (2) las **skills en vivo
impersonadas** contra Nextcloud (Calendar/Deck/Files, Bloques 2.1–2.4), que devuelven
datos **frescos** bajo la identidad del usuario (ADR-016).

Ninguna de esas fuentes cubre los datos **estructurados y agregados** que ya viven en
la **BD central del dashboard corporativo** (`dashboard_db`, MySQL en VPS3): tareas,
**horas** registradas, **desempeño**, **evaluaciones** (RRHH) y espejos/derivados de
Deck (`tasks` / `deck_*`). El bot no puede responder "¿cuántas horas registré esta
semana?", "¿cómo va mi desempeño?" o "mis tareas del dashboard" porque **no lee esa
BD**.

¿Cómo se integra esa fuente estructurada **sin acoplar** la skill al driver MySQL, sin
**romper** la arquitectura hexagonal y sin **duplicar la autoridad** de las skills en
vivo (que deben seguir mandando sobre los datos frescos de Nextcloud)?

## Decisión

Un puerto **`DashboardPort`** en `services/` —contrato mínimo de las consultas
**read-only** que las skills necesitan— **+ UN adapter MySQL concreto** en `adapters/`
que lo implementa contra `dashboard_db`. La skill depende del **puerto**, no del driver.
Los value objects (p. ej. `HoursSummary`, `DashboardTask`, `PerformanceSummary`) viven
en `domain/`. Es la **misma receta que ADR-002** (`LLMPort`) y que los adapters de
Calendar/Deck/Files.

### Es un `RetrievalPort` estructurado, hermano del RAG — NO sustituto de las skills

`DashboardPort` es una **fuente de contexto** (recuperación de datos para responder),
**hermano** del puerto de recuperación del RAG: donde el RAG recupera **texto no
estructurado**, `DashboardPort` recupera **filas estructuradas** (agregaciones, joins,
filtros exactos). Ambos **aportan contexto, no ejecutan acciones**.

**No sustituye a las skills en vivo.** Calendar/Deck/Files impersonados siguen siendo
**autoritativos para los datos frescos de Nextcloud** (estado actual de un board,
eventos de hoy, archivos). El dashboard responde lo **suyo** (horas, desempeño,
evaluaciones, reportes/histórico). La regla explícita de qué fuente responde qué está
en **ADR-023**.

### Alcance read-only; escritura como stubs comentados

El adapter **solo hace `SELECT`**. Doble candado: el usuario de BD tiene **solo
`SELECT`** (ADR-022), así que la escritura es imposible aunque el código la intentara.
Las operaciones de escritura se dejan como **stubs comentados** en el puerto
(documentadas, **no** implementadas) para no cerrar el diseño, pero **sin habilitar
efectos**: activar escritura sería una decisión explícita con su propio ADR y su propio
gate de credenciales, nunca un accidente. El dashboard es un sistema que **otros
procesos escriben**; el bot solo lee.

### Identidad obligatoria en cada consulta

Toda consulta del puerto lleva la **identidad resuelta** del usuario como parámetro
estructural (no opcional) y filtra por ella; el detalle (regla de oro, mapeo
`nc_user_id → users.id`, rehúse de guests) está en **ADR-021**. `DashboardPort` **no
expone** ningún método de "query libre" ni ninguna consulta sin filtro de identidad.

## Consecuencias

- **Misma receta que ADR-002** aplicada a MySQL: el dominio/servicio no conoce el
  driver; cambiarlo o mockearlo es **un adapter**. Tests **sin red** con fixtures.
- **Tercera fuente de contexto** junto al RAG (texto) y las skills en vivo (Nextcloud
  fresco). Exige la **regla de autoridad de ADR-023** para no dar respuestas
  contradictorias.
- **Read-only por diseño y por `GRANT`** (ADR-022): candado doble. La escritura queda
  como stubs comentados; habilitarla es una decisión futura explícita.
- **Skill delgada** (ADR-018): una skill `consultar_dashboard` orquesta el puerto y
  queda fina; respeta la regla de capas (ARCHITECTURE §3).
- **Nueva dependencia**: un driver MySQL (p. ej. `PyMySQL`/`aiomysql`/`mysqlclient`) que
  se importa **de forma perezosa** solo si `dashboard_ready` (ADR-022), como el RAG y
  AppAPI — el import de `main.py` no lo exige cuando el dashboard está deshabilitado.
- **Deuda nueva D9 (propuesta) — acoplamiento a un esquema cross-repo.** `dashboard_db`
  es propiedad de **otro equipo/repo**; sus nombres de tabla/columna pueden cambiar
  fuera de nuestro control. Mitigación: el **adapter es la ÚNICA capa** que conoce el
  esquema (el dominio habla en value objects), **tests de contrato** con fixtures del
  esquema real, y —a futuro— **vistas SQL estables** (contrato versionado) que aíslen al
  bot de refactors internos del dashboard.

## Alternativas descartadas

- **Ingerir `dashboard_db` en el RAG** (embeddings de filas): pierde la **estructura**
  (agregaciones, joins, filtros exactos por identidad) y la frescura; el RAG es para
  **texto no estructurado**. Viola SRP y no sirve para "¿cuántas horas?".
- **Reusar `ExternalConnectorPort` (ADR-019, CRM)**: el CRM es un sistema con su propia
  API/SDK y semántica de **escritura**; `dashboard_db` es una BD relacional **read-only
  interna**. Forzarlos en un mismo puerto mezcla ejes de variación (SRP). Se **reevalúa**
  si algún día convergen, pero **YAGNI** ahora.
- **Skill que habla directo al driver MySQL**: acopla la lógica al proveedor, no es
  testeable sin red y repite el antipatrón que **ADR-002** descartó para OpenAI.
- **Escritura del bot al dashboard**: fuera de alcance. El dashboard es la fuente que
  otros procesos escriben; el bot **lee**. La escritura sería otro ADR + otro gate.
- **Replicar `dashboard_db` a VPS2**: sobre-ingeniería para una lectura puntual; añade
  sync/consistencia (el mismo antipatrón que ADR-006 marcó para el corpus). Reevaluable
  solo si la latencia VPS2↔VPS3 lo exige (ver ADR-022).
