# `secrets/` — material sensible del túnel a `dashboard_db` (Bloque 3)

Todo lo de esta carpeta está **gitignored** (ver `.gitignore`) salvo este README.
**Nunca** comitees llaves, `known_hosts` ni notas con credenciales/IPs de infra.

El sidecar `db-tunnel` (`jnovack/autossh`, ver `docker-compose.yml`, Patrón B de ADR-022)
monta estos archivos **read-only**:

| Archivo | Qué es | Cómo generarlo |
|---------|--------|----------------|
| `db_tunnel_key` | Llave **privada** SSH del usuario `gcf_tunnel` en VPS3 (montada en `/id_rsa`). | `ssh-keygen -t ed25519 -f secrets/db_tunnel_key -N ''` y sube la `.pub` a `~gcf_tunnel/.ssh/authorized_keys` en VPS3 (idealmente con `command=`/`permitopen="127.0.0.1:33069"` restringido al forward). |
| `known_hosts` | Clave **pública de host** de VPS3, **precargada** (montada en `/known_hosts`; `StrictHostKeyChecking=yes`). | `ssh-keyscan -p 22 153.92.214.91 > secrets/known_hosts` y **verifica el fingerprint** por un canal fuera de banda antes de confiar en él. |

## Variables en `.env` (gitignored)

El bot y el sidecar leen estas variables (ver `.env.example`). El `docker-compose.yml` ya
trae los valores **verificados** como defaults; solo el **password es obligatorio** en
`.env`:

```
# Sidecar db-tunnel (SSH a VPS3) — defaults ya en compose, overridea si cambian:
VPS3_SSH_HOST=153.92.214.91
VPS3_SSH_PORT=22
TUNNEL_SSH_USER=gcf_tunnel
DASHBOARD_DB_REMOTE_PORT=33069   # MySQL en 127.0.0.1 de VPS3

# Bot → MySQL a través del sidecar:
DASHBOARD_DB_HOST=db-tunnel
DASHBOARD_DB_PORT=3306
DASHBOARD_DB_NAME=dashboard_db
DASHBOARD_DB_USER=gcf_bot_ro     # read-only (GRANT SELECT), NO root — ADR-022
DASHBOARD_DB_PASSWORD=           # ⚠ usa la password YA ROTADA (no la comprometida)
```

El compose mapea estas a las env vars nativas de `jnovack/autossh`
(`SSH_REMOTE_HOST`/`SSH_MODE=-L`/`SSH_TARGET_PORT`/…), produciendo exactamente
`autossh -M 0 -L 0.0.0.0:3306:127.0.0.1:33069 gcf_tunnel@153.92.214.91` con
`ServerAliveInterval=30`, `ServerAliveCountMax=3`, `ExitOnForwardFailure=yes` y
`StrictHostKeyChecking=yes` contra el `known_hosts` precargado.

## Notas de seguridad (ADR-021/022)

- **Rotación:** la password anterior de `gcf_bot_ro` quedó **comprometida** — usa la
  **rotada**. No la escribas en ningún archivo versionado; solo en `.env`.
- `gcf_bot_ro` debe tener **solo `GRANT SELECT`** sobre `dashboard_db` y estar acotado a
  origen `127.0.0.1` (`gcf_bot_ro@127.0.0.1`), por eso el forward emerge en **localhost
  de VPS3**.
- La skill filtra **toda** query por la identidad del usuario (regla de oro, ADR-021); el
  usuario read-only es la **defensa en profundidad**.
- `db-tunnel` es **deuda operativa D10**: fragilidad de reconexión, rotación de la llave
  del túnel, VPS3 como dependencia de disponibilidad. Migrar a **red privada (Patrón A)**
  cuando infra la provea deja obsoleto este sidecar. Si no usas el dashboard, comenta el
  servicio `db-tunnel` y su entrada en `depends_on` del bot.
```
