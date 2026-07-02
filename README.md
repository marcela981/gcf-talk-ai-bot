# GCF Talk AI Bot

ExApp para Nextcloud que conecta una conversación de Talk con un modelo
de lenguaje (OpenAI por defecto). Se registra como bot de Talk vía AppAPI,
recibe los eventos de chat por webhook autenticado con HMAC-SHA256 y
responde únicamente cuando es mencionado por su `display_name`. La
aplicación es *stateless*: el historial de la conversación lo persiste
Talk, no la ExApp.

## Diagrama de despliegue

```
                           ┌────────────────────────────────────────┐
                           │            Host Docker                 │
                           │                                        │
                           │  ┌──────────────────────┐              │
   Usuario  ──HTTPS──▶ NPM │  │ nextcloud-nextcloud-1│              │
                           │  │  Nextcloud 32.0.7.1  │              │
                           │  │   ├─ AppAPI 32       │              │
                           │  │   └─ Talk 22.0.9     │              │
                           │  └──────────┬───────────┘              │
                           │             │ vps2DockerNet            │
                           │             │ (bridge)                 │
                           │  ┌──────────▼───────────┐              │
                           │  │  gcf-talk-ai-bot     │              │
                           │  │  FastAPI :8080       │──HTTPS──▶ OpenAI API
                           │  │  nc_py_api[app]      │              │
                           │  └──────────────────────┘              │
                           └────────────────────────────────────────┘
```

El bot **no publica puertos al host**; toda la comunicación con Nextcloud
ocurre dentro de la red Docker `vps2DockerNet`. AppAPI resuelve al
contenedor por nombre (`gcf-talk-ai-bot:8080`) gracias al DNS de Docker.

## Prerrequisitos

| Componente      | Versión mínima | Notas                                          |
|-----------------|----------------|------------------------------------------------|
| Nextcloud       | 28             | Probado en 32.0.7.1                            |
| AppAPI          | 32             | Habilitado y operativo (`occ app:list`)        |
| Talk            | 22             | Probado en 22.0.9                              |
| Docker Engine   | 24             | Con plugin `compose`                           |
| OpenAI API key  | —              | Cuenta con cuota disponible                    |

Adicionalmente: red Docker compartida con el contenedor de Nextcloud
(en este despliegue, `vps2DockerNet`).

## Despliegue

### 1. Clonar el repositorio en el host

```bash
git clone <repo-url> gcf-talk-ai-bot
cd gcf-talk-ai-bot
```

### 2. Crear `.env` a partir del ejemplo

```bash
cp .env.example .env
```

Generar el secreto compartido (mismo valor que se entrega a AppAPI al
registrar el deploy daemon):

```bash
openssl rand -hex 32
```

Editar `.env` y completar `APP_SECRET`, `OPENAI_API_KEY` y, si procede,
`OPENAI_MODEL`, `BOT_DISPLAY_NAME` y `BOT_DESCRIPTION`.

### 3. Registrar el deploy daemon `manual-install` en AppAPI

El daemon debe vivir **en la misma red Docker** que el contenedor del bot
para que AppAPI pueda resolverlo por nombre. El host del daemon es el
`container_name` **sin puerto** (AppAPI añade `:8080` por su cuenta).

```bash
docker exec --user www-data <nextcloud_container> \
  php occ app_api:daemon:register \
    manual_install \
    "Manual install" \
    manual-install \
    http \
    gcf-talk-ai-bot \
    http://gcf-talk-ai-bot \
    --net vps2DockerNet
```

### 4. Construir y arrancar el contenedor

```bash
docker compose up -d --build
```

### 5. Registrar la ExApp en AppAPI

```bash
docker exec --user www-data <nextcloud_container> \
  php occ app_api:app:register \
    gcf_talk_ai_bot \
    manual_install \
    --json-info '{
      "appid":"gcf_talk_ai_bot",
      "name":"GCF Talk AI Bot",
      "daemon_config_name":"manual_install",
      "version":"0.1.0",
      "secret":"<APP_SECRET de .env>",
      "host":"gcf-talk-ai-bot",
      "port":8080,
      "scopes":["TALK","TALK_BOT","FILES"],
      "system_app":false
    }' \
    --force-scopes \
    --wait-finish
```

### 6. Verificación

```bash
docker exec --user www-data <nextcloud_container> \
  php occ talk:bot:list
```

Debe aparecer una fila con el `display_name` configurado y estado
`enabled`. Si el comando no muestra el bot, revisar la sección
**Troubleshooting**.

## Operación

### Habilitar el bot en una sala

En la UI de Talk: *Conversation settings → Bots → Enable* sobre el bot
que acabas de registrar. Una vez habilitado, mencionarlo con
`@"GCF AI Bot"` (o el `BOT_DISPLAY_NAME` configurado) para iniciar
una conversación.

### Logs

```bash
docker logs -f gcf-talk-ai-bot
```

### Heartbeat

Desde dentro de la red Docker compartida:

```bash
curl http://gcf-talk-ai-bot:8080/heartbeat
```

Respuesta esperada: `{"status":"ok"}`.

## Skills (modo agente)

Con `AGENT_ENABLED=true`, el LLM enruta por **tool-calling** (ADR-017/ADR-018): en vez
de responder solo con texto, puede invocar *skills*. Cada skill se cablea según su
**propia dependencia**, no un interruptor global:

- **Base de conocimiento** (`consultar_base_conocimiento`) — sii RAG configurado
  (`PGVECTOR_DSN` + `OPENAI_API_KEY`).
- **Skills de Nextcloud** (calendario, Deck, archivos) — sii `appapi_ready`
  (`NEXTCLOUD_URL` + `APP_ID` + `APP_SECRET`). Cada una construye su **propio cliente
  HTTP firmado** (`AUTHORIZATION-APP-API`) y actúa **impersonando** al usuario que
  menciona al bot (ADR-016): resuelve su `uid` de Talk y opera bajo SU identidad, así
  que solo ve/toca lo que ese usuario ya puede.

Si el registro queda vacío (sin RAG ni AppAPI) o `AGENT_ENABLED=false`, el bot degrada
a la ruta de texto puro de la Fase 1/2.

Las **5 skills de Nextcloud** activas bajo `appapi_ready`:

| Skill | Tipo | Bloque | Qué hace |
|-------|------|--------|----------|
| `consultar_calendario` | lectura | 2.1 | Lista eventos del calendario (un día o rango), incluyendo recurrencias. |
| `agendar_evento` | escritura | 2.2 | Crea un evento (CalDAV) con título, fecha y hora. |
| `consultar_deck` | lectura | 2.3 | Lista tableros de Deck y las tarjetas de un tablero/columna (por defecto solo las **asignadas a quien pregunta**; `solo_mias=false` para ver todas). |
| `crear_tarjeta_deck` | escritura | 2.3 | Crea una tarjeta en una columna de un tablero. |
| `consultar_archivos` | lectura | 2.4 | Lista/busca archivos y lee el contenido de texto de uno. |

> **Identidad requerida:** las skills de Nextcloud exigen que el actor de Talk tenga
> `uid` local (`users/<uid>`); invitados/federados se **rehúsan** con un mensaje claro.
>
> **Estado de la escritura:** `agendar_evento` y `crear_tarjeta_deck` están implementadas
> y unit-testeadas, pendientes de la **validación runtime** (smoke) — ver
> `docs/adr/ADR-016`. **Fuera de alcance por ahora:** escritura de Files (Bloque 2.4b) y
> asignación de usuarios en Deck (Bloque 2.3b, requiere resolver nombre→uid).
>
> **Scopes:** el conjunto de skills de Nextcloud usa los scopes `TALK`, `TALK_BOT` y
> `FILES` (declarados en `appinfo/info.xml`); `FILES` es el que necesita `consultar_archivos`
> (la lectura CalDAV/Deck ya funcionó con `TALK`/`TALK_BOT` en el spike de impersonation).
> Regístralos al dar de alta la ExApp (paso 5).

## Tests

```bash
pytest -q
```

La suite cubre la política de trigger, el constructor de prompts, el
servicio de conversación y la verificación HMAC.

## Troubleshooting

Fallos reales observados durante la Fase 1 y su solución definitiva:

### 1. AppAPI busca el bot en `localhost` y no lo encuentra

**Síntoma:** al registrar la ExApp, AppAPI reporta `connection refused`
contra `127.0.0.1:8080`.

**Causa:** el deploy daemon `manual-install` se registró sin
`--net`, por lo que AppAPI ejecuta sus *checks* desde el contenedor de
Nextcloud asumiendo loopback.

**Fix:** registrar el daemon con `--net vps2DockerNet` (o la red
Docker compartida correspondiente).

### 2. AppAPI concatena `:8080:8080` al host del daemon

**Síntoma:** errores DNS contra `gcf-talk-ai-bot:8080:8080`.

**Causa:** se incluyó el puerto en el campo `host` del daemon. AppAPI
añade `:APP_PORT` por su cuenta.

**Fix:** registrar el host **sin puerto** (`gcf-talk-ai-bot`, no
`gcf-talk-ai-bot:8080`).

### 3. `nc_py_api` síncrono bajo FastAPI async

**Síntoma:** el `enabled_handler` no se completa; AppAPI marca la
ExApp como *enabled* pero `talk:bot:list` no la muestra.

**Causa:** `nc_py_api` 0.30 deprecó el `TalkBot` síncrono. Bajo un
`lifespan` async, `set_handlers` no espera correctamente al handler
síncrono.

**Fix:** pinear `nc_py_api[app]>=0.30,<0.31` y migrar todo el camino a
async: `AsyncTalkBot`, `AsyncNextcloudApp`, `async def enabled_handler`.

### 4. `AttributeError` en `TalkBotMessage.send_message`

**Síntoma:** `AttributeError: 'TalkBotMessage' object has no attribute
'send_message'` al intentar responder.

**Causa:** se invocaba el método sobre el `TalkBotMessage` recibido del
webhook.

**Fix:** enviar la respuesta a través del propio bot, pasando el
mensaje como referencia para construir un *quoted reply*:

```python
await bot.send_message(reply, message)
```

## Variables de entorno

| Variable             | Requerido | Default                                              | Descripción                                                  |
|----------------------|-----------|------------------------------------------------------|--------------------------------------------------------------|
| `APP_SECRET`         | sí        | —                                                    | Secreto compartido con AppAPI. Firma HMAC de los webhooks.   |
| `OPENAI_API_KEY`     | sí        | —                                                    | API key de OpenAI.                                           |
| `OPENAI_MODEL`       | no        | `gpt-4o-mini`                                        | Modelo de chat completions a usar.                           |
| `BOT_DISPLAY_NAME`   | no        | `GCF AI Bot`                                         | Nombre que aparece en Talk y dispara el `@mention`.          |
| `BOT_DESCRIPTION`    | no        | `AI-powered assistant using OpenAI ChatGPT.`         | Descripción del bot en la lista de Talk.                     |
| `APP_ID`             | sí        | `gcf_talk_ai_bot`                                    | Identificador de la ExApp. Lo inyecta `docker-compose.yml`.  |
| `APP_VERSION`        | sí        | `0.1.0`                                              | Versión declarada a AppAPI.                                  |
| `APP_HOST`           | sí        | `0.0.0.0`                                            | Interfaz de escucha del FastAPI.                             |
| `APP_PORT`           | sí        | `8080`                                               | Puerto interno expuesto por el contenedor.                   |
| `NEXTCLOUD_URL`      | sí        | `http://nextcloud-nextcloud-1`                       | URL interna de Nextcloud dentro de la red Docker compartida. |
| `AA_VERSION`         | sí        | `32.0.0`                                             | Versión de AppAPI declarada a `nc_py_api`.                   |
| `APP_PERSISTENT_STORAGE` | sí    | `/data`                                              | Punto de montaje del volumen persistente.                    |
| `AGENT_ENABLED`      | no        | `false`                                              | Activa el modo agente (tool-calling / skills). Con `false`, ruta de texto Fase 1/2. |
| `BOT_DEFAULT_TZ`     | no        | `America/Bogota`                                     | Zona IANA del usuario para las skills de calendario/Deck (define "hoy" y presenta horas en local). |
| `FILES_READ_MAX_BYTES` | no      | `262144`                                             | Límite (bytes) para leer texto con `consultar_archivos`; excesos/binarios se rechazan. |

> Esta tabla lista el núcleo de despliegue y las variables de las skills de Nextcloud.
> Las variables de RAG (`PGVECTOR_DSN`, `EMBEDDING_MODEL`, `RAG_*`, …) y de memoria
> conversacional (`CONVERSATION_*`) están documentadas en `.env.example`.
