# ADR-016 — Modelo de identidad para skills: impersonation vía `nc.set_user`

| | |
|--|--|
| **Estado** | **Aceptado** (2026-06-30) — validado por `docs/spikes/SPIKE_IMPERSONATION.md` |
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
  que la disparó**, aplicando `nc.set_user(uid)` (verificado en el spike de Files:
  `nc_py_api` `nextcloud.py:509-517`). El `uid` impersonado viaja **dentro** de
  `AUTHORIZATION-APP-API` como `base64(uid:app_secret)`, **no** en un header
  `EX-APP-USER-ID` (corrección del spike, ver nota más abajo).

## Decisión

**Impersonation.** Cada ejecución de skill resuelve el `uid` del actor de Talk y
actúa **bajo esa identidad**: `nc.set_user(uid)` para las llamadas a Nextcloud, y
el mismo `uid` viaja al `ExternalConnectorPort` (ADR-019) para la autorización
aguas abajo. **El ExApp NO usa una cuenta de servicio con permisos agregados.**

### Mecanismo de impersonation (corregido por spike)

La identidad impersonada **no** viaja en un header `EX-APP-USER-ID` (ese header
**no existe** en `nc_py_api` 0.30.1). `nc.set_user(uid)` fija `_session._user` y el
hook `_add_auth` del adaptador HTTP embebe el `uid` **dentro** de
`AUTHORIZATION-APP-API` como `base64(f"{uid}:{app_secret}")`
(`_session.py:587-590`). El `app_secret` nunca se loguea.

### HaRP: recomendado para producción, NO bloqueante

> **Corregido por el spike** (`SPIKE_IMPERSONATION.md`): la versión previa de este
> ADR marcaba la migración `manual_install`→HaRP como **precondición bloqueante**.
> El spike la **falsa**: la impersonation fue honrada en `manual_install` (sin
> HaRP) — `set_user`→`identity` 200 con `server_resolved_id=mmazo`, y lectura OK de
> Calendar (207) y Deck (200). Por tanto **el camino de lectura no está bloqueado
> por HaRP**.

HaRP pasa a ser **recomendado para el endurecimiento de producción** (es el camino
soportado de AppAPI y porque **DSP — Default System Proxy — se retira en NC35**),
pero **no es bloqueante** para esta iteración. La migración queda como trabajo de
endurecimiento, no como trabajo previo obligatorio.

> Lo que el spike **no** validó: la **escritura** impersonada (skills con efectos).
> Antes de habilitar cualquier skill con efectos hay que confirmar que el `uid`
> impersonado puede crear/modificar recursos, no sólo leerlos.
>
> **Actualización (Bloques 2.2/2.3):** el *camino* de escritura ya está
> **implementado** detrás del Port para Calendar (`create_event`, CalDAV PUT) y Deck
> (`create_card`, REST POST), con cliente firmado propio (D-IMP-1) y tests sin red.
> Falta sólo la **validación runtime** (primer `create` real). Ver
> «Estado de la escritura impersonada (Bloques 2.2/2.3)» más abajo.

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
- **Lectura impersonada validada; escritura implementada, pendiente de smoke.** El
  spike confirma lectura impersonada (Calendar 207, Deck 200) en `manual_install`,
  así que las skills de **solo-lectura** quedan habilitadas por identidad. La
  **escritura** ya tiene camino **implementado y unit-testeado** (Calendar 2.2,
  Deck 2.3) con el **mismo mecanismo** de cliente firmado; el gate que queda es la
  **validación runtime** (primer `create` real), no HaRP. Como ambas superficies
  comparten mecanismo, **un solo smoke valida ambas**. Ver «Estado de la escritura
  impersonada (Bloques 2.2/2.3)».
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

## Scopes AppAPI (validado por spike para lectura)

Los _scopes_ actuales son `TALK` y `TALK_BOT` (README §5). El spike confirmó
`scopes_missing == []`: para la **lectura** impersonada de identidad, Calendar
(CalDAV) y Deck (REST) **no hizo falta ningún scope adicional**.

- `set_user` / impersonation — **OK con los scopes actuales** (sin HaRP).
- `FILES` (si una skill lee/escribe archivos) — **TBD** (no ejercido en el spike).
- `NOTIFICATIONS`, `ACTIVITIES`, calendario/tareas para **escritura** — **TBD**;
  la escritura impersonada no se validó y puede exigir _scopes_ extra.

## Estado de la escritura impersonada (Bloques 2.2/2.3)

El spike fue **read-only**; la escritura quedó como gate. Estado actual — el camino
está **implementado y unit-testeado**, a falta de la corrida real:

- **Calendar — crear evento (Bloque 2.2).** `CalendarPort.create_event` →
  `NextcloudCalendarAdapter.create_event`: **PUT** de un VEVENT (`.ics`) a
  `/remote.php/dav/calendars/<uid>/<calendario>/<uid-evento>.ics` con `If-None-Match: *`
  (crear-sin-pisar) y `DTSTART`/`DTEND` en `TZID` + `VTIMEZONE`. Status crudo como
  dato (201/204 ok; 403/409/412 → resultado de error, sin excepción cruda).
- **Deck — crear tarjeta (Bloque 2.3).** `DeckPort.create_card` →
  `DeckRestAdapter.create_card`: resuelve board→id y stack→id (por id o título) vía GET
  y hace **POST** `/index.php/apps/deck/api/v1.0/boards/{boardId}/stacks/{stackId}/cards`
  (`OCS-APIRequest`, `Content-Type: application/json`; body `title`/`type`/`order` +
  `description`/`duedate` opcionales). Status crudo como dato (200/201 ok; 400/403/404 →
  resultado de error).

Ambos caminos:

- respetan **D-IMP-1**: **cliente HTTP propio firmado** detrás del Port
  (`AUTHORIZATION-APP-API = base64(uid:app_secret)`), **sin** `nc._session.adapter` ni
  imports de `app/_spike`; el `app_secret` **nunca** se loguea;
- reportan el fallo como **dato** (no excepción cruda para los rechazos HTTP esperables);
- están cubiertos por **tests sin red** (`httpx.MockTransport`): ruta, cabeceras de
  impersonation, cuerpo y mapeo de status.

**Comparten el mecanismo de escritura impersonada** (mismo header firmado, misma forma
de reportar status). Por tanto **un solo smoke real valida ambas superficies**: si el
primer `create_event`/`create_card` es honrado por Nextcloud bajo la identidad
impersonada, la escritura queda confirmada para Calendar y Deck. Hasta ese smoke, la
escritura está **implementada y unit-testeada, pero NO validada en runtime** (sigue
siendo el gate, no HaRP).

> **Fuera de alcance (Bloque 2.3b):** la **asignación de usuarios** a una tarjeta de
> Deck requiere resolver **nombre→uid** y NO está implementada; `create_card` no la
> expone aún.
>
> **Files (Bloque 2.4):** la **lectura** impersonada (WebDAV `PROPFIND`/`GET` vía
> cliente firmado propio, D-IMP-1) ya está implementada y unit-testeada
> (`FilesPort` + `NextcloudFilesAdapter` + skill `consultar_archivos`, SOLO lectura).
> El NO-GO de ADR-006 aplicaba **solo** a Files como **corpus RAG** (ingesta/sync
> frágil), **no** a esta lectura puntual; su **primer PROPFIND real** valida la
> lectura WebDAV impersonada (comparte mecanismo con la lectura CalDAV ya probada).
> La **escritura** de Files (subir/mover/borrar) queda para el Bloque **2.4b**,
> compartiendo este mismo gate de validación de escritura impersonada (D-IMP-2).

## Deuda registrada

- **D-IMP-1 · Las skills NO deben usar `nc._session.adapter`.** El spike accedió a
  CalDAV y Deck a través de los adaptadores HTTP privados
  `nc._session.adapter` / `adapter_dav` porque `nc_py_api` 0.30.1 no expone
  raw-request público (sólo `ocs()` y las APIs typed de Files/Sharing). Es
  acoplamiento a atributo privado, aceptable **sólo** para código desechable. Una
  skill productiva debe hablar con Nextcloud a través de un **cliente HTTP propio
  firmado detrás del Port** (o de APIs OCS/typed cuando existan), nunca tocando
  `nc._session.adapter`. Gate de diseño para ADR-018/ADR-019.
  **Estado:** honrada por Calendar (2.2) y Deck (2.3), que construyen su propio
  cliente firmado detrás del Port (ver «Estado de la escritura impersonada»).
- **D-IMP-2 · Validación runtime de escritura impersonada pendiente.** El camino de
  escritura (Calendar `create_event`, Deck `create_card`) está implementado y
  unit-testeado sin red, pero **no** se ha ejercido contra un Nextcloud real. Falta un
  **smoke** que cree un evento y una tarjeta bajo la identidad impersonada y confirme
  que el servidor lo honra (y que no faltan _scopes_ de escritura). Ambas superficies
  comparten mecanismo ⇒ un solo smoke las cubre. Es el gate para dar por validada la
  escritura.
- **D-IMP-3 · Builder de cabeceras firmadas duplicado.** `NextcloudCalendarAdapter` y
  `DeckRestAdapter` replican el mismo `_headers(uid)` (token `AUTHORIZATION-APP-API`).
  Duplicación **deliberada** (cada adapter encapsula su propio cliente; no se refactorizó
  para no tocar Calendar al añadir Deck). Candidato a extraer a un helper compartido
  (p. ej. `adapters/appapi_auth.py`) cuando aparezca una tercera superficie.
