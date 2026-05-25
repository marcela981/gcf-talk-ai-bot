# SPIKE — Nextcloud Files como Source of Truth del corpus RAG

> Spike de viabilidad técnica previo al cierre de **ADR-006**.
> Repo: `gcf-talk-ai-bot` (ExApp Nextcloud Talk, Python).
> Librería pinneada: `nc_py_api[app]>=0.30,<0.31` (verificado en local: **0.30.1**).
> Modo: **read-only** sobre Nextcloud. Código bajo `app/_spike/` es desechable.
> Rama propuesta: `spike/nextcloud-files`.
> Fecha: 2026-05-22.

---

## 1. Resumen ejecutivo

Validamos siete hipótesis sobre si `nc_py_api` 0.30.x basta para usar Nextcloud Files como corpus del RAG de Fase 2.

- **Lo estructural pasa**: `AsyncNextcloudApp` expone `files`, `files.sharing`, tags y la API de impersonation (`set_user`) en su superficie async. El spike importa sin errores con la versión pinneada (verificado con `python -c "import app.main"`).
- **Lo runtime queda pendiente de tu ejecución**: p50/p95 reales, comportamiento exacto frente al grupo "finanzas" sin sesión de usuario, y validación de tags/shares contra los PDFs reales requieren correr el spike contra el stack vivo (sección §6).
- **El único hueco serio** que ya identifico estáticamente es **H7**: `nc_py_api` 0.30.x **no expone un endpoint de cambios incrementales (sync token)**. Hay que vivir con polling por etag de carpeta o subir a registrar `Files Events` vía `nc.providers` (no trivial, deuda nueva).

**Veredicto preliminar (sujeto a métricas reales): GO condicionado.** Condiciones en §7.

---

## 2. Inspección de la librería (Tarea 1)

### 2.1 Módulos relevantes en `nc_py_api` 0.30.1

| Capa | Archivo | Clase / objeto async |
|---|---|---|
| Cliente raíz ExApp | `nc_py_api/nextcloud.py:440` | `AsyncNextcloudApp(_AsyncNextcloudBasic)` |
| Files | `nc_py_api/files/files_async.py:35` | `AsyncFilesAPI` (atributo `nc.files`) |
| Sharing | `nc_py_api/files/sharing.py:130` | `_AsyncFilesSharingAPI` (atributo `nc.files.sharing`) |
| Tags | `nc_py_api/files/files_async.py:356-371` | Métodos sobre `AsyncFilesAPI` (no es clase aparte) |
| Tipos | `nc_py_api/files/__init__.py` | `FsNode`, `SystemTag`, `Share`, `ShareType`, `FilePermissions` |
| FastAPI glue ExApp | `nc_py_api/ex_app/integration_fastapi.py:74` | `atalk_bot_msg`, `anc_app`, `AppAPIAuthMiddleware` |

### 2.2 Signatures que uso en el spike

| Operación | Signature (versión async, `nc_py_api` 0.30.1) | Evidencia |
|---|---|---|
| Cambiar identidad del ExApp | `async AsyncNextcloudApp.set_user(self, user_id: str) -> None` | `nextcloud.py:509-517` |
| Leer identidad actual | `@property async AsyncNextcloudApp.user -> str` | `nextcloud.py:502-507` |
| Listar carpeta | `async AsyncFilesAPI.listdir(self, path: str \| FsNode = "", depth: int = 1, exclude_self=True) -> list[FsNode]` | `files_async.py:45-59` |
| Descarga en streaming a archivo | `async AsyncFilesAPI.download2stream(self, path: str \| FsNode, fp, **kwargs) -> None` | `files_async.py:97-106` |
| Descarga buffer en memoria (no usada) | `async AsyncFilesAPI.download(self, path: str \| FsNode) -> bytes` | `files_async.py:90-95` |
| Tags por archivo | `async AsyncFilesAPI.get_tags(self, file_id: FsNode \| int) -> list[SystemTag]` | `files_async.py:362-371` |
| Listar tags del sistema | `async AsyncFilesAPI.list_tags(self) -> list[SystemTag]` | `files_async.py:356-360` |
| Shares de un path | `async _AsyncFilesSharingAPI.get_list(self, shared_with_me=False, reshares=False, subfiles=False, path: str \| FsNode = "") -> list[Share]` | `sharing.py:143-163` |
| Shares heredados | `async _AsyncFilesSharingAPI.get_inherited(self, path: str) -> list[Share]` | `sharing.py:171-175` |

### 2.3 Lo que NO existe en async

- `Nextcloud.loginflow_v2` y la versión **sync** `Nextcloud`/`NextcloudApp` están marcadas como deprecadas y desaparecen en 0.31 (`nextcloud.py:73-78`). No las uso.
- **No hay método `get_changes` / sync token** en `AsyncFilesAPI`. El módulo `files_async.py` cubre CRUD + tags + locking + favorites + trashbin + versions, pero no incremental sync. Búsqueda: `grep "sync" nc_py_api/files/files_async.py` no devuelve nada relevante.

---

## 3. Spike funcional (Tarea 2)

### 3.1 Artefactos creados

| Archivo | Propósito |
|---|---|
| `app/_spike/__init__.py` | Marca el paquete como SPIKE — REMOVE BEFORE MERGE. |
| `app/_spike/nextcloud_files_spike.py` | `async run_spike()` — fases (a)..(e), prueba de impersonation (H6), salida JSON. |
| `app/main.py:127` | Middleware `AppAPIAuthMiddleware(disable_for=["debug/files-spike"])` — bypass del shared-secret sólo para esa ruta. Marcado SPIKE — REMOVE BEFORE MERGE. |
| `app/main.py:152-159` | Endpoint `POST /debug/files-spike`, registrado **solo** si `SPIKE_FILES_ENABLED=1`. Marcado SPIKE — REMOVE BEFORE MERGE. |

### 3.2 Inputs por variable de entorno

| Var | Default | Razón |
|---|---|---|
| `SPIKE_FILES_ENABLED` | (no) | Gate. Sin esto, ni siquiera se registra el endpoint. |
| `SPIKE_FILES_ROOT_PATH` | `AI-Corpus/finanzas` | Carpeta a inspeccionar, sin slash inicial. |
| `SPIKE_FILES_OWNER_UID` | `admin` | Bajo qué uid resuelve nc_py_api los paths DAV. |
| `SPIKE_IMPERSONATE_AS` | (vacío) | Si se pasa, `set_user(...)` y repite `listdir` (prueba H6). |
| `SPIKE_ITERATIONS` | `5` | N para p50/p95. |

### 3.3 Snippet representativo (≤ 30 líneas)

Caso central: listar la carpeta, descargar el primer PDF en streaming y medir tiempos. Extraído de `app/_spike/nextcloud_files_spike.py:75-110` (recortado para legibilidad).

```python
async def run_spike() -> dict[str, Any]:
    nc = AsyncNextcloudApp()                              # reads APP_SECRET / NEXTCLOUD_URL from env
    await nc.set_user(_env("SPIKE_FILES_OWNER_UID", "admin"))   # impersonate folder owner

    root = _norm_path(_env("SPIKE_FILES_ROOT_PATH", "AI-Corpus/finanzas"))
    children = await nc.files.listdir(root, depth=1, exclude_self=True)

    pdf = next(n for n in children if not n.is_dir and n.info.mimetype == "application/pdf")
    with open(f"/tmp/spike_{pdf.name}", "wb") as fp:
        await nc.files.download2stream(pdf, fp)           # 5 MiB chunks by default

    tags   = await nc.files.get_tags(pdf)                 # list[SystemTag]
    shares = await nc.files.sharing.get_list(path=root)   # list[Share] incl. ShareType.TYPE_GROUP

    return {
        "files":  [{"name": n.name, "etag": n.etag, "size": n.info.size,
                    "mime": n.info.mimetype, "mtime": n.info.last_modified.isoformat()}
                   for n in children],
        "tags":   [t.display_name for t in tags],
        "shares": [{"to": s.share_with, "type": s.share_type.name,
                    "perm": int(s.permissions)} for s in shares],
    }
```

### 3.4 Métricas

Estructura del bloque `e_metrics` que devuelve el endpoint:

```json
"e_metrics": {
  "listdir":         {"iterations": 5, "p50_ms": <TBD>, "p95_ms": <TBD>, "min_ms": <TBD>, "max_ms": <TBD>, "samples_ms": [...]},
  "stream_download": {"iterations": 5, "p50_ms": <TBD>, "p95_ms": <TBD>, "min_ms": <TBD>, "max_ms": <TBD>, "samples_ms": [...]}
}
```

**Métricas medidas:** _pendientes — completar tras ejecutar el spike (ver §6)._

> No validable desde esta sesión sin acceso al stack Docker `vps2DockerNet`/`nextcloud-nextcloud-1`. Sin esto, p50/p95 quedan en suspenso y deben rellenarse al pegar el JSON de respuesta del endpoint.

---

## 4. Prueba de impersonation / scope (Tarea 3)

### 4.1 Cómo construye nc_py_api la identidad del ExApp

`AsyncNextcloudApp()` (sin args) lee del entorno `APP_ID`, `APP_SECRET`, `NEXTCLOUD_URL`, `AA_VERSION` y construye una `AsyncNcSessionApp`. Esa sesión autentica con **shared secret**, no con credenciales de usuario (`integration_fastapi.py:368` usa `AsyncNextcloudApp()` en el middleware sin pasar user). Por defecto la identidad expuesta por DAV es **vacía / "admin"** según cómo Nextcloud resuelva los headers `EX-APP-ID + EX-APP-USER-ID + AUTHORIZATION-APP-API`.

Para impersonar, el ExApp llama `await nc.set_user(uid)` (`nextcloud.py:509-517`). Esto cambia el `user_id` del session adapter y reconfigura caches (`talk.config_sha`, `activity.last_given`, etc.). A partir de ahí, **toda llamada DAV usa `files/<uid>/...`** y Nextcloud evalúa permisos como si fuera ese usuario, incluida la pertenencia a grupos.

### 4.2 Lo que valida el spike

El spike toma dos identidades en una sola corrida:

1. **Fase (a)..(e)** con `set_user(SPIKE_FILES_OWNER_UID)` → el dueño absoluto de `/AI-Corpus`. Debe ver todo.
2. **Bloque `impersonation`** con `set_user(SPIKE_IMPERSONATE_AS)` → un miembro del grupo "finanzas". Debe ver los archivos **compartidos con ese grupo** dentro de su propio root (paths típicos: `files/user_finanzas/AI-Corpus/finanzas/...` por reflejo del share, o `files/user_finanzas/finanzas/...` según cómo Nextcloud monte el share del grupo).

> ⚠ Importante: si la carpeta está compartida **al grupo** pero el spike usa la ruta `AI-Corpus/finanzas` con `user_path` relativo al usuario impersonado, **puede 404** si el share aterriza con otro nombre o en otro nivel. La forma robusta de validar H6 es comparar `files.by_id(fileid_visto_por_owner)` desde la identidad impersonada — eso esquiva problemas de path. Si el spike falla en H6, ese es el siguiente intento.

### 4.3 Identidad efectiva por request

| Identidad efectiva | Cuándo |
|---|---|
| App user (shared-secret, sin user) | `AsyncNextcloudApp()` recién construido sin `set_user()`. **No traversa DAV de usuario** — las llamadas DAV requieren `files/<uid>/`. |
| Owner uid (`SPIKE_FILES_OWNER_UID`) | Tras `set_user(owner)`. Ve todo bajo `files/owner/`. |
| Group member (`SPIKE_IMPERSONATE_AS`) | Tras `set_user(member)`. Ve sólo lo que esté en `files/member/` (lo propio + lo compartido al grupo del cual es miembro). |

**No hay "el ExApp lee como admin sin pasar por nadie"** salvo que el dueño de `/AI-Corpus` sea efectivamente `admin`. El ExApp no es un superuser DAV.

---

## 5. Detección de cambios (Tarea 4)

### 5.1 ¿Existe sync token en `nc_py_api` 0.30.x?

**No.** `nc_py_api/files/files_async.py` no expone `sync-collection` ni un endpoint de changes. WebDAV de Nextcloud sí soporta `<oc:sync-collection>` y `<sync-token/>` a bajo nivel (RFC 6578 + extensiones OC), pero la librería no lo envuelve.

### 5.2 Fallbacks viables

1. **Polling por etag de carpeta raíz (recomendado).**
   - `FsNode.etag` cambia cuando cambia el contenido recursivo de un directorio en Nextcloud.
   - Estrategia: cada N minutos `await nc.files.by_path("AI-Corpus")`; si `etag != etag_anterior`, disparar reindex incremental (sub-listdir con depth profundo, comparar `last_modified` por archivo).
   - **Costo**: 1 PROPFIND por ciclo, barato.
   - **Limitación**: si dos cambios se intercalan dentro de un ciclo de polling, los detecta como uno. Aceptable para RAG (no es realtime).

2. **Push vía AppAPI Providers (`nc.providers`)** — `nc_py_api/ex_app/providers/`.
   - Registrar el ExApp como listener de eventos de Files (`files_actions_menu`, eventos DAV) requiere endpoint propio que reciba `ActionFileInfo` o `ActionFileInfoEx` (`files/__init__.py:513-572`).
   - **Costo**: agregar un endpoint nuevo + lógica de dedupe, otro item de deuda.
   - **Limitación**: Nextcloud entrega eventos best-effort; si el ExApp está caído cuando ocurre el evento, lo pierde. Push **no** sustituye polling, lo complementa.

3. **REPORT/PROPFIND `<oc:sync-collection>` por HTTP directo (httpx).**
   - Saltarse `nc_py_api` y hablar WebDAV crudo. Da `sync-token` real y diffs incrementales.
   - **Costo**: reimplementar firma de request del ExApp manualmente (cabeceras AppAPI), perder el contrato de `nc_py_api`. **No vale la pena** salvo que polling no escale.

### 5.3 Decisión preliminar

Para Fase 2 arrancar con **(1) polling por etag** cada 5–15 min sobre la raíz del corpus. Si el volumen documental crece o los SLA de freshness se aprietan, sumar **(2) push providers** como señal de "reindex ya" sin abandonar polling.

> **Nada de esto se implementa en este spike** — sólo se documenta la conclusión, según las restricciones.

---

## 6. Cómo ejecutar el spike (lo corres tú)

Comandos exactos. Asume que tu working tree de `gcf-talk-ai-bot` ya contiene los cambios y el contenedor está corriendo.

```bash
# 1. Crear la rama spike — TÚ lo haces (working tree sucio: .env.example
#    modificado + README/ARCHITECTURE untracked siguen como tenías).
cd C:\Marcela\GCF\gcf-talk-ai-bot
git checkout -b spike/nextcloud-files
git add app/_spike/__init__.py app/_spike/nextcloud_files_spike.py \
        app/main.py docs/spikes/SPIKE_NEXTCLOUD_FILES.md
git commit -m "spike(files): nextcloud files viability for ADR-006"

# 2. Habilitar el endpoint y dar contexto a la corrida. Editar .env del
#    deployment (NO comiteado) y agregar las cuatro vars:
#       SPIKE_FILES_ENABLED=1
#       SPIKE_FILES_ROOT_PATH=AI-Corpus/finanzas
#       SPIKE_FILES_OWNER_UID=admin                    # quien creó /AI-Corpus
#       SPIKE_IMPERSONATE_AS=user_finanzas             # opcional, para H6

# 3. Rebuild + restart del contenedor del bot.
docker compose up -d --build gcf-talk-ai-bot

# 4. Disparar el spike desde un contenedor en la misma red interna
#    (vps2DockerNet). El bot NO está publicado al host.
docker run --rm --network vps2DockerNet curlimages/curl:8.10.1 \
    -sS -X POST http://gcf-talk-ai-bot:8080/debug/files-spike \
    -H "Content-Type: application/json" -d '{}' \
    > spike-result.json

# 5. (Alternativa sin endpoint) Ejecutar el módulo dentro del contenedor.
#    Útil si el middleware bypass te incomoda y prefieres no exponerlo.
docker exec -it gcf-talk-ai-bot \
    python -m app._spike.nextcloud_files_spike > spike-result.json

# 6. Pégame `spike-result.json` y completo §3.4 y los ✅/❌/⚠ de §8.
```

Después de tener el JSON:
- Rellena la columna **p50_ms** / **p95_ms** de §3.4.
- Marca H1..H7 con ✅/❌/⚠ en §8.
- Si todo da verde, mueves el veredicto de §7 de **GO condicionado** a **GO firme**.

---

## 7. Veredicto sobre ADR-006

### Veredicto preliminar

**GO condicionado.**

Estáticamente la librería expone lo que necesitamos y el spike importa sin errores (verificado: `python -c "import app.main"` + `python -c "SPIKE_FILES_ENABLED=1 ... app.main.APP.routes"` lista `/debug/files-spike`).

### Condiciones para que el GO sea firme (todas medibles con el spike corrido)

1. **H2 (listdir devuelve mtime + etag + size + mime)** → ✅ en el JSON.
2. **H3 (download2stream a /tmp funciona y `bytes_on_disk` == `FsNode.size`)** → ✅.
3. **H5 (al menos un Share con `share_type == "TYPE_GROUP"` y permisos legibles)** → ✅.
4. **`e_metrics.listdir.p95_ms` < 500 ms y `e_metrics.stream_download.p95_ms` < 3 s para un PDF de tamaño "normal" (< 5 MB).** Si el p95 de listdir > 1 s, considerar cache local de FsNode por etag.
5. **H6 (la identidad impersonada lee los archivos del grupo)** → ✅ o, si falla por path, → ✅ vía `files.by_id(fileid_del_owner)`.

### Razones por las que el veredicto NO es **GO firme** ya

- **H7 (sync incremental nativo)**: ❌ confirmado por inspección. Vivimos con polling+etag. No es bloqueante para Fase 2 pero es deuda.
- **Métricas runtime**: no se midieron desde esta sesión.

### Si el spike termina en **NO-GO**

Causas previsibles y fallbacks:

| Falla | Causa probable | Fallback |
|---|---|---|
| `listdir` p95 > 2 s con < 50 archivos | Latencia de PROPFIND alta; Nextcloud expuesto en una red lenta | Cache de FsNodes por etag de carpeta; trabajar contra una réplica DAV; pre-warm. |
| `download2stream` se cuelga | Storage backend lento o nc_py_api buffering en memoria a pesar de stream | WebDAV directo via `httpx.AsyncClient` con `stream=True`. |
| `get_tags` devuelve `[]` para PDF con tag asignado | API de tags requiere capability ausente | Llamar OCS `dav/systemtags-relations/files/<fileid>/` directamente. |
| Impersonation no lee los archivos del grupo | El share aterriza en un path distinto al esperado | Usar `files.by_id(fileid_del_owner)` desde la identidad impersonada. Si tampoco resuelve, replantear el modelo de scoping (no usar grupos de Nextcloud, ir a tabla propia de ACL). |
| Endpoint `/debug/files-spike` devuelve 401 | El middleware no respetó `disable_for` | Ejecutar la vía alternativa (`docker exec ... python -m app._spike.nextcloud_files_spike`). |

### Métodos `nc_py_api` que iremos a usar en producción (asumiendo GO)

- `AsyncNextcloudApp` + `set_user(uid)` — single per-request o per-job impersonation. `nextcloud.py:509`.
- `nc.files.listdir(path, depth=N)` para descubrimiento del corpus. `files_async.py:45`.
- `nc.files.by_path(path)` / `nc.files.by_id(fileid)` para resolver nodos sin doble PROPFIND. `files_async.py:61-74`.
- `nc.files.download2stream(node, fp)` para alimentar el extractor (PDF/DOCX). `files_async.py:97`.
- `nc.files.get_tags(node)` para enriquecer metadatos del chunk (tag → ACL adicional). `files_async.py:362`.
- `nc.files.sharing.get_list(path=...)` y `.get_inherited(path)` para construir el ACL del nodo. `sharing.py:143`, `sharing.py:171`.
- `FsNode.etag` como clave de cache + invalidación incremental. `files/__init__.py:212`.

---

## 8. Tabla de hipótesis

Las hipótesis se derivan directamente de las tareas 1–4 del spike. Las marcas finales (✅/❌/⚠) requieren la corrida real (sección §6).

| ID | Hipótesis | Estado | Evidencia |
|---|---|---|---|
| H1 | `nc_py_api` 0.30.1 expone Files + Tags + Sharing en superficie **async**. | ✅ | `nc_py_api/files/files_async.py:35` (clase `AsyncFilesAPI`); `nc_py_api/files/sharing.py:130` (`_AsyncFilesSharingAPI`); `nc_py_api/nextcloud.py:440` (`AsyncNextcloudApp` agrega `files`, `files.sharing`). |
| H2 | `listdir(...)` devuelve `name + mtime + etag + size + mime` por entrada. | ⚠ no validable sin ejecución | Signatures y propiedades existen (`FsNode.name`, `info.last_modified`, `etag`, `info.size`, `info.mimetype` en `files/__init__.py:199-266`). Falta confirmar que **todos** vengan poblados contra el Nextcloud real — algunos backends devuelven `mimetype=""` para ciertos tipos. Sin esto el veredicto queda en suspenso. |
| H3 | Descarga en streaming a un `fp` local funciona con chunks (no carga el PDF entero en memoria). | ⚠ no validable sin ejecución | `download2stream` delega en `_session.download2stream(path, fp, dav=True, **kwargs)` con `chunk_size` configurable (default 5 MiB, `files_async.py:97-106`). Falta verificar `bytes_on_disk == FsNode.size`. |
| H4 | `get_tags(node)` devuelve `SystemTag[]` para un archivo etiquetado. | ⚠ no validable sin ejecución | Método existe (`files_async.py:362-371`). Requiere capability `systemtags`; nc_py_api no la chequea explícitamente — si falla, el error vendrá de DAV. Sin esto el veredicto queda en suspenso. |
| H5 | `sharing.get_list(path=...)` devuelve al menos un `Share` con `share_type == ShareType.TYPE_GROUP` y `permissions` legibles para `/AI-Corpus/finanzas/`. | ⚠ no validable sin ejecución | `sharing.py:143-163` async; `ShareType.TYPE_GROUP = 1` (`files/__init__.py:374-399`). Sin ejecución no sé si la carpeta está realmente compartida al grupo (depende del setup operacional). |
| H6 | Un ExApp con scope `FILES` puede leer archivos compartidos sólo con el grupo "finanzas" **impersonando** al miembro del grupo (no como app-only). | ⚠ no validable sin ejecución | `AsyncNextcloudApp.set_user(uid)` cambia el user_id de la sesión DAV (`nextcloud.py:509-517`). Sin `set_user` el ExApp no tiene path DAV de usuario que recorrer — no es "admin universal". La identidad efectiva se reporta en `report["identity"]["after_set_user"]` y en el bloque `impersonation` del JSON. |
| H7 | `nc_py_api` 0.30.x expone un sync token / endpoint de cambios incrementales. | ❌ | `nc_py_api/files/files_async.py` no contiene métodos `sync_collection`/`get_changes` (verificado por inspección de las 531 líneas del archivo). Fallback: polling por `FsNode.etag` de la raíz; alternativa push: registrar listener vía `nc.providers` (otro endpoint, otra deuda — fuera de scope del spike). |

---

## 9. Próximos pasos para ti

1. Crear la rama y commitear (ver §6 paso 1).
2. Activar `SPIKE_FILES_ENABLED=1` + setear las otras 3 vars.
3. Rebuild y disparar el endpoint (o `docker exec` el módulo).
4. Pegarme el JSON resultante.
5. Yo rellenarle §3.4 (métricas) y §8 (✅/❌/⚠ definitivos), y pasarle el veredicto final del ADR-006.
6. Cuando se cierre el spike: borrar `app/_spike/`, revertir `disable_for=[...]` y el bloque `# SPIKE — REMOVE BEFORE MERGE` de `app/main.py`. La rama queda como referencia histórica; **no** se mergea.
