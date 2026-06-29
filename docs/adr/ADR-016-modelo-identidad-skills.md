# ADR-016 — Modelo de identidad para skills: impersonation vía `nc.set_user`

| | |
|--|--|
| **Estado** | **Propuesto** (2026-06-29) |
| **Iteración** | Motor de agente / skills (sucesora de la Fase 2 RAG) |
| **Relacionados** | ADR-001 (`manual-install`), ADR-003 (stateless), ADR-011 (scoping por rol), ADR-014 (Opción C descartada), ADR-017, ADR-018, ADR-019 |

## Problema

Hasta la Fase 2 la ExApp opera con **una sola identidad**: el _shared secret_
del ExApp, **sin usuario** asociado. El RAG recupera con un `role_scope` **fijo**
(`corporate`, ADR-011) y la memoria es un buffer por sala (ADR-014). Ninguna de
esas rutas actúa "en nombre de" nadie: sólo leen recursos propios del ExApp.

En cuanto las skills (ADR-017 / ADR-018) ejecuten **acciones** contra Nextcloud
o sistemas corporativos a petición del usuario que menciona al bot (crear una
tarea, leer su calendario, consultar el CRM), aparece la pregunta de identidad:
**¿con qué identidad se ejecuta la acción?**

- **(a) Cuenta de servicio** — un `uid` técnico único, con permisos agregados,
  ejecuta todas las skills de todos los usuarios.
- **(b) Impersonation** — la acción se ejecuta **bajo la identidad del usuario
  que la disparó**, propagando `EX-APP-USER-ID` y aplicando
  `nc.set_user(uid)` (verificado en el spike de Files: `nc_py_api`
  `nextcloud.py:509-517`).

## Decisión

**Impersonation.** Cada ejecución de skill resuelve el `uid` del actor de Talk y
actúa **bajo esa identidad**: `nc.set_user(uid)` para las llamadas a Nextcloud, y
el mismo `uid` viaja al `ExternalConnectorPort` (ADR-019) para la autorización
aguas abajo. **El ExApp NO usa una cuenta de servicio con permisos agregados.**

### Precondición bloqueante: migración `manual_install` → HaRP

La impersonation por `EX-APP-USER-ID` exige desplegar el ExApp **tras HaRP** (el
proxy de AppAPI), no en el modo `manual_install` actual (ADR-001, README §3). La
migración `manual_install`→HaRP queda registrada como **trabajo previo
bloqueante** de esta iteración. Mientras no exista HaRP, ninguna skill **con
efectos** se habilita (ver Consecuencias).

> El detalle exacto del mecanismo (versión mínima de AppAPI/HaRP, headers y
> _scopes_ que habilitan `set_user` para una ExApp `TALK_BOT`) es **inferencia
> pendiente de validar por spike**, no un hecho verificado en este repo.

### Mapeo Talk `actor_id` → `uid`

Talk entrega `actor_id` con forma `<tipo>/<id>` (el prefijo `bots/` está
confirmado en código: `conversation_service.py:142`, filtro anti-loop de
ADR-014). El mapeo a identidad impersonable es:

| `actor_id` | Resolución | Soporte de skills |
|------------|------------|-------------------|
| `users/<uid>` | `uid` local impersonable | **Sí** |
| `guests/<hash>` | invitado anónimo, **sin uid local** | **No** (la skill se rehúsa con mensaje claro) |
| `bridged/…`, `federated_users/…` | sin uid local en esta instancia | **No** (TBD por spike) |
| `bots/…` | otro bot | Ya filtrado antes del agente (anti-loop, ADR-014) |

> Los prefijos distintos de `bots/` se infieren del modelo de actores de Talk;
> sólo `bots/` está verificado en código. **Guests no soportados** es decisión
> firme; el resto se confirma por spike.

## Consecuencias

- **Autorización delegada al sistema.** Nextcloud (y el sistema externo)
  evalúan permisos como el **usuario real**. No reinventamos ACLs: el _blast
  radius_ de una skill ≡ lo que el usuario ya puede hacer. Se cierra el flanco
  de una cuenta omnipotente.
- **Trazabilidad.** Las acciones aparecen ejecutadas por el usuario, no por un
  `uid` técnico opaco. Auditoría natural.
- **Convierte el scope fijo en derivable.** Disponer del `uid` permite, a
  futuro, derivar el `role_scope` real desde los grupos de Nextcloud — la nota
  **PENDIENTE** de ADR-011 y parte de **D4**. (Esa derivación es trabajo aparte,
  fuera de este ADR.)
- **Guests / federados sin skills.** Aceptado: las skills son acciones
  corporativas, no para invitados anónimos.
- **Bloqueada por HaRP.** Hasta migrar, las skills **de solo-lectura sin
  identidad** (p. ej. consultar el corpus) podrían correr con la identidad del
  ExApp, pero **toda skill con efectos permanece deshabilitada**.
- **Stateless preservado (ADR-003).** `set_user` se aplica **por ejecución** y no
  persiste entre webhooks (consistente con la prueba H6 del spike de Files). El
  único estado entre requests sigue siendo el buffer de ADR-014.

## Alternativas descartadas

- **Cuenta de servicio única.** Un `uid` técnico con permisos amplios ejecuta
  todas las skills. Descartada por **seguridad y gobernanza**: concentra
  privilegios, rompe la trazabilidad (todo aparece hecho por el bot), y obliga a
  mantener una matriz de permisos propia que **duplica** la de Nextcloud. Es la
  misma "Opción C" que ADR-014 ya marcó como mayor superficie y gobernanza.
- **Sin identidad (sólo _shared secret_ del ExApp).** Sólo sirve para leer
  recursos del propio ExApp; no puede actuar en nombre de nadie ni respetar ACLs
  por usuario. Insuficiente en cuanto una skill tiene efectos.

## Scopes AppAPI candidatos (TBD — confirmar por spike)

Los _scopes_ actuales son `TALK` y `TALK_BOT` (README §5). La impersonation y las
skills concretas probablemente exijan _scopes_ adicionales. **Todos TBD hasta el
spike:**

- _Scope(s)_ que habiliten `set_user` / `EX-APP-USER-ID` bajo HaRP — **TBD**.
- `FILES` (si una skill lee/escribe archivos) — **TBD**.
- `NOTIFICATIONS`, `ACTIVITIES`, calendario/tareas (según skills concretas) — **TBD**.

> No se asume ningún _scope_ nuevo como hecho. El spike fija la lista mínima.
