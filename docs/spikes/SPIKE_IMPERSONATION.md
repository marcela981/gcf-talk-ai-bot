# SPIKE — Impersonation del usuario invocante contra Calendar y Deck

> Spike de viabilidad técnica para **ADR-016** (modelo de identidad para skills).
> Repo: `gcf-talk-ai-bot` (ExApp Nextcloud Talk, Python).
> Librería pinneada: `nc_py_api[app]>=0.30,<0.31` (verificado en local: **0.30.1**).
> Modo: **read-only** sobre Nextcloud. Código bajo `app/_spike/impersonation/` es desechable (`SPIKE — REMOVE BEFORE MERGE`).
> Gate: `SPIKE_IMPERSONATION_ENABLED=1` (sin esto, el router ni se registra).
> Fecha: 2026-06-29.

---

## 1. Resumen ejecutivo

ADR-016 decidió **impersonation** (actuar bajo la identidad del usuario que
menciona al bot) en vez de una cuenta de servicio, y dejó como **TBD a confirmar
por spike**: (a) que `set_user` no sea rechazado en este stack, (b) que Calendar
(CalDAV) sea legible impersonado, (c) que Deck (REST) sea legible impersonado, y
(d) los _scopes_ AppAPI que falten.

Este spike construye un `AsyncNextcloudApp`, llama `set_user(SPIKE_TARGET_UID)` y
ejerce tres endpoints **bajo esa identidad**, capturando el **status HTTP crudo**
de cada uno (sin el envoltorio de excepciones de `nc_py_api`, para que un 401/403
aparezca como **dato**, no como error lanzado).

- **Lo estructural pasa estáticamente**: el spike importa y compila con la versión
  pinneada; el parser CalDAV y la inferencia de _scopes_ están validados offline
  (`python -c "from app._spike.impersonation.probe import _parse_calendars"`).
- **Lo runtime queda pendiente de tu ejecución**: los status reales de `set_user`,
  PROPFIND de calendarios y GET de Deck **requieren correr contra el stack vivo**
  (§5). Sin eso, H1/H2/H3 quedan en ⚠.
- **Hallazgo estático que ya cambia ADR-016**: en `nc_py_api` 0.30.1 la identidad
  impersonada **no** viaja en un header `EX-APP-USER-ID`; viaja **dentro** de
  `AUTHORIZATION-APP-API` como `base64(uid:app_secret)` (ver §2.2). ADR-016
  asumía `EX-APP-USER-ID`; corregir esa nota según lo que confirme el runtime.

**Veredicto preliminar: PENDIENTE (sujeto a tu corrida).** Condiciones en §6.

---

## 2. Cómo funciona la impersonation en `nc_py_api` 0.30.1

### 2.1 `set_user` y la identidad efectiva

| Operación | Signature (0.30.1) | Evidencia |
|---|---|---|
| Impersonar | `async AsyncNextcloudApp.set_user(self, user_id: str)` | `nextcloud.py:509-517` |
| Leer identidad configurada | `@property async AsyncNextcloudApp.user -> str` | `nextcloud.py:501-507` |

`set_user(uid)` hace dos cosas relevantes: fija `self._session._user = uid`
**y** dispara `update_server_info()` → una llamada OCS a
`/ocs/v1.php/cloud/capabilities` **bajo la nueva identidad**
(`nextcloud.py:511-517`). Es decir: si la impersonation **no** está habilitada
en el stack, el rechazo puede aflorar ya en `set_user`. El spike lo envuelve en
`try/except` y reporta `set_user_ok` (el `_user` queda fijado igual, así que las
sondas siguientes corren impersonadas aunque ese OCS falle).

### 2.2 El header que transporta el `uid` (hallazgo)

El adaptador HTTP añade en cada request un hook `_add_auth`
(`_session.py:587-590`):

```
AUTHORIZATION-APP-API: base64( f"{self._user}:{self.cfg.app_secret}" )
```

→ **el `uid` impersonado va embebido en `AUTHORIZATION-APP-API`**, no en un
`EX-APP-USER-ID` separado (ese header **no existe** en esta versión de la
librería). El `app_secret` **nunca** se loguea en el spike. Cómo el servidor
(AppAPI/HaRP) mapea ese `uid` a una sesión real es justo lo que H1 valida en
runtime.

### 2.3 Por qué se usan los adaptadores internos `nc._session.adapter*`

`nc_py_api` 0.30.1 **no expone** un método público de "request arbitrario": sólo
`ocs()` (desenvuelve el sobre OCS y **lanza** en no-2xx) y las APIs tipadas de
Files/Sharing. CalDAV y la REST de Deck no son ni OCS ni Files, así que el spike
usa directamente los adaptadores niquests subyacentes:

| Adaptador | `base_url` | Uso en el spike |
|---|---|---|
| `nc._session.adapter` | `cfg.endpoint` (raíz NC) | OCS `cloud/user`, Deck REST |
| `nc._session.adapter_dav` | `cfg.dav_endpoint` = `endpoint + /remote.php/dav` | PROPFIND de calendarios |

Es **acoplamiento a un atributo privado**, aceptable para código desechable. El
patrón `adapter_dav.request("PROPFIND", …, data=…, headers=…)` es el mismo que el
propio módulo Files usa internamente (`files/files_async.py:84`).

> **Deuda implícita para producción**: una skill productiva **no** debe depender
> de `nc._session.adapter`. Si Calendar/Deck pasan a producción, hace falta o un
> cliente HTTP propio firmado, o que `nc_py_api` exponga raw-request, o usar las
> APIs OCS/typed cuando existan. Registrar al cerrar el spike.

---

## 3. Qué ejerce el spike (Tarea principal)

| Sonda | Llamada | Hipótesis | Lectura |
|---|---|---|---|
| `identity` | `GET /ocs/v1.php/cloud/user` | H1 | `server_resolved_id` debería == `SPIKE_TARGET_UID` |
| `calendar` | `PROPFIND /remote.php/dav/calendars/<uid>/` (Depth 1) | H2 | lista de colecciones con `<cal:calendar/>` en `resourcetype` |
| `deck` | `GET /index.php/apps/deck/api/v1.0/boards` | H3 | array JSON de boards (`id`, `title`) |

Por cada llamada el reporte trae: `http_status`, `configured_identity` (el `uid`
efectivo en ese momento), `ok`, y —si es 401/403— `denied: true` + `error_excerpt`
(primeros 400 chars del cuerpo). Un fallo de transporte (DNS/conexión) se reporta
como `transport_error` sin tumbar el resto de sondas.

### 3.1 Artefactos creados

| Archivo | Propósito |
|---|---|
| `app/_spike/impersonation/__init__.py` | Marca el paquete como SPIKE. |
| `app/_spike/impersonation/probe.py` | `async run_probe()` — set_user + 3 sondas + inferencia de scopes. Lógica pura de parseo testeable offline. |
| `app/_spike/impersonation/router.py` | `APIRouter` con `POST /debug/impersonation-spike`. |
| `app/_spike/impersonation/__main__.py` | Entry one-shot: `python -m app._spike.impersonation`. |
| `app/main.py` (bloque `SPIKE`) | `include_router(...)` **condicional** a `SPIKE_IMPERSONATION_ENABLED=1`. Único cambio en `main.py`. |

### 3.2 Inputs por variable de entorno

| Var | Default | Razón |
|---|---|---|
| `SPIKE_IMPERSONATION_ENABLED` | (no) | Gate del router HTTP. El módulo `-m` no lo necesita. |
| `SPIKE_TARGET_UID` | (vacío) | uid del usuario a impersonar. **Requerido**; vacío ⇒ `fatal` en el reporte. |
| `SPIKE_IMPERSONATION_DECK_PATH` | `/index.php/apps/deck/api/v1.0/boards` | Override del endpoint de Deck por si cambia la versión de la API. |

### 3.3 Forma del JSON de salida (campos clave)

```json
{
  "spike": "impersonation",
  "read_only": true,
  "app": {"app_id": "...", "aa_version": "...", "endpoint": "...", "dav_endpoint": "..."},
  "impersonation_mechanism": "… AUTHORIZATION-APP-API = b64(uid:secret) …",
  "set_user_ok": true,
  "configured_identity": "<uid>",
  "calls": {
    "identity": {"http_status": <TBD>, "server_resolved_id": "<TBD>", "ok": <TBD>},
    "calendar": {"http_status": <TBD>, "calendar_count": <TBD>, "calendars": [...]},
    "deck":     {"http_status": <TBD>, "board_count": <TBD>, "boards": [...]}
  },
  "scopes_missing": []
}
```

---

## 4. Nota sobre autenticación del endpoint HTTP

A diferencia del spike de Files, **no** se añadió esta ruta al `disable_for` del
`AppAPIAuthMiddleware` (la instrucción fue tocar `main.py` **sólo** para el
registro condicional del router). Por tanto, `POST /debug/impersonation-spike`
queda **detrás** del middleware de _shared secret_ y exige cabeceras AppAPI
válidas para alcanzarse.

**Vía recomendada (sin fricción de auth):** el módulo one-shot, que construye el
cliente con las env vars del ExApp y **no** pasa por FastAPI ni el middleware:

```bash
docker exec -it gcf-talk-ai-bot python -m app._spike.impersonation > impersonation-result.json
```

> Si prefieres la vía HTTP, habría que añadir temporalmente
> `"debug/impersonation-spike"` al `disable_for` de `main.py` — **no** lo hice,
> por la restricción. Queda a tu criterio.

---

## 5. Cómo ejecutar el spike (lo corres tú)

```bash
# 1. (working tree) commitea los artefactos del spike en una rama.
cd C:\Marcela\GCF\gcf-talk-ai-bot
git checkout -b spike/impersonation
git add app/_spike/impersonation/ app/main.py docs/spikes/SPIKE_IMPERSONATION.md
git commit -m "spike(identity): impersonation vs Calendar/Deck for ADR-016"

# 2. setea el uid a impersonar en el .env del deployment (NO comiteado):
#       SPIKE_TARGET_UID=<uid_de_un_usuario_real>     # p. ej. jdoe
#    (SPIKE_IMPERSONATION_ENABLED solo hace falta para la vía HTTP)

# 3. rebuild + restart del contenedor del bot.
docker compose up -d --build gcf-talk-ai-bot

# 4. corre el módulo one-shot dentro del contenedor (vía recomendada).
docker exec -it gcf-talk-ai-bot python -m app._spike.impersonation > impersonation-result.json

# 5. (alternativa HTTP) requiere SPIKE_IMPERSONATION_ENABLED=1 y cabeceras AppAPI
#    válidas (ver §4). Por eso se prefiere el paso 4.
```

Después de tener el JSON:
- Pégame `impersonation-result.json` y completo §3.3 y los ✅/❌/⚠ de §7.
- Si `set_user_ok=false` o `identity.denied=true` → H1 ❌ ⇒ la impersonation **no**
  está habilitada en este modo de despliegue (probablemente falta HaRP, según la
  precondición de ADR-016).

---

## 6. Veredicto sobre ADR-016 (parte de identidad)

### Veredicto preliminar

**PENDIENTE.** El código está listo y validado estáticamente; el veredicto de
viabilidad depende de los status HTTP reales (§5).

### Condiciones para un **GO** de impersonation

1. **H1** — `set_user_ok=true` **y** `identity.http_status==200` con
   `server_resolved_id == SPIKE_TARGET_UID`.
2. **H2** — `calendar.http_status==207` con `calendar_count >= 1`.
3. **H3** — `deck.http_status==200` con `board_count >= 0` (un array vacío
   también prueba acceso: 200 sin 401/403).
4. `scopes_missing == []` o, si no, los _scopes_ candidatos quedan identificados y
   se añaden al registro del ExApp (`occ app_api:app:register --json-info`).

### Si termina en **NO-GO**

| Falla | Causa probable | Siguiente paso |
|---|---|---|
| `set_user` lanza / `identity` 401 | Impersonation no honrada en `manual_install` | Migrar a **HaRP** (precondición ADR-016) y re-correr. |
| `calendar` 403 | Falta scope DAV/CalDAV para la ExApp | Confirmar y añadir el scope; re-correr. |
| `deck` 401/403 | Deck no expone su REST a ExApps impersonadas, o falta capability | Revisar si Deck requiere sesión de usuario real; evaluar alternativa (OCS/typed). |
| `deck` 404 | Ruta/versión de API distinta | Ajustar `SPIKE_IMPERSONATION_DECK_PATH`. |
| Calendar 207 pero `calendar_count==0` | El PROPFIND apunta a la home equivocada | Probar PROPFIND al principal (`/principals/users/<uid>/`) para `calendar-home-set`. |

---

## 7. Tabla de hipótesis

Las marcas finales (✅/❌/⚠) requieren la corrida real (§5).

| ID | Hipótesis | Estado | Evidencia / Validación |
|---|---|---|---|
| H1 | `set_user(uid)` **no** es rechazado (no 401/403) bajo el despliegue actual; el servidor resuelve la identidad al `uid` impersonado. | ⚠ no validable sin ejecución | `set_user` dispara un OCS bajo la nueva identidad (`nextcloud.py:511-517`); la sonda `identity` cruza `server_resolved_id` vs `SPIKE_TARGET_UID`. **Riesgo conocido**: la precondición de ADR-016 dice que la impersonation por header requiere **HaRP**, no `manual_install` — si es así, H1 dará ❌ hasta migrar. |
| H2 | Calendar (CalDAV) es **legible** impersonado: PROPFIND a `/calendars/<uid>/` devuelve 207 con ≥1 calendario. | ⚠ no validable sin ejecución | `adapter_dav.request("PROPFIND", …)` (mismo patrón que `files_async.py:84`); parser de multistatus validado offline (home omitida, 2 calendarios detectados en muestra). Falta el 207 real. |
| H3 | Deck (REST) es **legible** impersonado: `GET …/deck/api/v1.0/boards` devuelve 200 con un array de boards. | ⚠ no validable sin ejecución | `adapter.get(deck_path)` con `OCS-APIRequest: true` (header por defecto del adaptador, `_session.py:168`). Falta confirmar que Deck honra la identidad impersonada y no exige sesión de usuario real. |

---

## 8. Próximos pasos para ti

1. Crear la rama y commitear (§5 paso 1).
2. Setear `SPIKE_TARGET_UID` con un usuario real (§5 paso 2).
3. Rebuild y correr el módulo one-shot (§5 paso 4).
4. Pegarme `impersonation-result.json`.
5. Yo relleno §3.3 y §7 (✅/❌/⚠), y emito el veredicto de la parte de identidad de
   ADR-016 (incluida la corrección del header `EX-APP-USER-ID` → `AUTHORIZATION-APP-API`).
6. Al cerrar: borrar `app/_spike/impersonation/`, revertir el bloque `SPIKE` de
   `app/main.py`. La rama queda como referencia; **no** se mergea.
