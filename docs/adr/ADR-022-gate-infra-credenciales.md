# ADR-022 — Gate de infraestructura + credenciales read-only para `dashboard_db`

| | |
|--|--|
| **Estado** | **Propuesto** (2026-07-02) — bloqueado por prerequisito de infra (ruta VPS2→VPS3) |
| **Iteración** | Integración con la BD del dashboard corporativo (Bloque 3) |
| **Relacionados** | ADR-001 (`manual-install`, topología Docker/red), ADR-016 (patrón de gate por dependencia propia), ADR-020 (`DashboardPort`), ADR-021 (identidad), config `appapi_ready`/`rag_enabled` (mismo patrón de gate), D10 (dependencia operativa nueva) |

## Problema

El adapter de ADR-020 necesita **conectarse a `dashboard_db`** (MySQL en **VPS3**) desde
el bot (**VPS2**). **Esa conexión no existe hoy**: no hay ruta de red entre VPS2 y VPS3
hacia el puerto de MySQL. Además, el bot **no debe** usar `root` ni un usuario con
escritura contra una BD corporativa central.

Antes de instanciar el Port hay que resolver **dos prerequisitos que NO son código**:
(1) la **ruta de red** VPS2→VPS3, y (2) un **usuario de BD dedicado, read-only**. ¿Cuál
es el requerimiento exacto de cada uno y cómo se refleja en la config del bot?

## Decisión

### 1. Gate de infra (prerequisito, responsabilidad de infra)

Habilitar la ruta **bot(VPS2) → MySQL(VPS3:3306)** por **una** de estas vías (a acordar
con infra; **el bot no la resuelve**):

- **(a) Red privada / VPN** entre VPS2 y VPS3 (preferida). O
- **(b) Firewall + allowlist** de la **IP de VPS2** hacia el puerto MySQL de VPS3, **con
  TLS** en la conexión.

**Nunca** exponer MySQL a Internet abierto. **Sin este gate, el `DashboardPort` no se
instancia** — el diseño queda listo pero inactivo.

### 2. Usuario de BD dedicado, read-only (mínimo privilegio)

El bot usa un **usuario MySQL propio** con **`GRANT SELECT`** sobre `dashboard_db`
(idealmente solo las tablas necesarias), **NO `root`**, **NO escritura**. Restringido por
**host origen** (la IP/host de VPS2) cuando sea posible. Es el **candado que hace
imposible** la escritura, complementando el adapter read-only (ADR-020) y el filtro de
identidad (ADR-021) — **defensa en profundidad**.

### 3. Config nueva `DASHBOARD_DB_*` (valores **TBD**)

Variables de entorno, **defaults vacíos** (el import nunca falla por una var ausente,
igual que RAG/AppAPI; el adapter valida sus credenciales en su primer uso):

| Var | Requerida para activar | Valor |
|-----|------------------------|-------|
| `DASHBOARD_DB_HOST` | sí | **TBD** (infra confirma host/IP de VPS3) |
| `DASHBOARD_DB_PORT` | no | `3306` (default MySQL) |
| `DASHBOARD_DB_NAME` | sí | **TBD** (p. ej. `dashboard_db`) |
| `DASHBOARD_DB_USER` | sí | **TBD** (usuario read-only dedicado) |
| `DASHBOARD_DB_PASSWORD` | sí | **TBD** (secreto; **nunca** se loguea) |
| `DASHBOARD_DB_SSL_*` | según vía (b) | **TBD** (CA/verificación si se exige TLS) |

Los `DASHBOARD_DB_*` quedan **TBD hasta que infra confirme la ruta**. El
`DASHBOARD_DB_PASSWORD` **nunca** aparece en logs; el DSN se construye sin exponer la
contraseña.

### 4. Gate `dashboard_ready` (análogo a `appapi_ready`)

Una propiedad de config **`dashboard_ready`** = `True` **sii** hay config mínima
(`DASHBOARD_DB_HOST` + `_NAME` + `_USER` + `_PASSWORD`). En el composition root
(`main.py`), la skill de dashboard se **registra sii `dashboard_ready`** (import perezoso
del driver MySQL). Si no, **degrada**: la skill no existe y el bot sigue operando con las
demás (RAG, Calendar, Deck, Files). Mismo patrón que `rag_enabled`/`appapi_ready`.

## Consecuencias

- **Orden de trabajo explícito:** (1) infra abre la ruta VPS2→VPS3 y crea el usuario
  read-only; (2) se rellenan `DASHBOARD_DB_*`; (3) `dashboard_ready` pasa a `True` y la
  skill se cablea. Hasta (1), **todo el diseño (ADR-020/021/023) está listo pero
  inactivo** — no hay regresión ni riesgo.
- **Seguridad por capas:** mínimo privilegio (`SELECT`), origen restringido, TLS (vía b),
  secreto no logueado. Combina con adapter read-only (ADR-020) y `WHERE` de identidad
  (ADR-021).
- **Config paralela a AppAPI/RAG:** mismo patrón de defaults vacíos + gate + import
  perezoso; no añade acoplamiento en el arranque.
- **Deuda nueva D10 (propuesta) — dependencia operativa:** VPS3 pasa a ser una
  **dependencia de disponibilidad** del bot para las respuestas de dashboard (si VPS3/BD
  cae, la skill degrada a error como dato, no tumba el bot). Añade **rotación de
  credenciales** y **gestión del secreto** a la operación. Registrar.

## Alternativas descartadas

- **Exponer MySQL a Internet con user/pass**: superficie de ataque inaceptable para una
  BD corporativa. Se exige red privada/VPN o firewall+allowlist+TLS.
- **Reusar `root` o el usuario de escritura del dashboard**: viola mínimo privilegio; un
  bug del bot podría escribir/borrar datos corporativos. Se exige un usuario **`SELECT`
  dedicado**.
- **Túnel SSH ad-hoc desde el contenedor**: frágil operacionalmente (gestión de llaves,
  reconexión). Se prefiere una ruta de red **gestionada por infra**; queda como *fallback*
  documentado si (a)/(b) no fueran viables a tiempo.
- **Replicar `dashboard_db` a VPS2** (réplica local): sobre-ingeniería para una lectura
  read-only puntual; añade sync/consistencia (antipatrón que ADR-006 marcó para el
  corpus). **Reevaluable** solo si la latencia VPS2↔VPS3 degrada la UX del bot.
- **Instanciar el Port siempre y fallar en runtime**: rompe el patrón de gate; se prefiere
  **no registrar** la skill sin `dashboard_ready` (degradación limpia).
