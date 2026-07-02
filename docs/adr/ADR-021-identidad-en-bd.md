# ADR-021 — Identidad en la BD del dashboard: la regla de oro (filtro obligatorio)

| | |
|--|--|
| **Estado** | **Propuesto** (2026-07-02) |
| **Iteración** | Integración con la BD del dashboard corporativo (Bloque 3) |
| **Relacionados** | ADR-016 (impersonation vía `nc.set_user`; `actor_id → impersonated_uid`), ADR-011 (scoping por rol), ADR-020 (`DashboardPort`), ADR-022 (usuario BD read-only), ADR-023 (fuente autoritativa), ARCHITECTURE §3 |

## Problema

Las skills en vivo actúan **bajo la identidad del usuario** (ADR-016:
`nc.set_user(uid)` → Nextcloud evalúa permisos como ese usuario). `dashboard_db`, en
cambio, es una **BD central que agrega datos de TODOS los usuarios**: tareas, horas,
desempeño y **evaluaciones de RRHH**. Una consulta **sin filtro de identidad**
devolvería datos de **terceros** — una fuga grave, especialmente en RRHH.

MySQL **no** aplica la identidad por nosotros (no hay `set_user` ni RLS nativo como en
Postgres). ¿Cómo se garantiza que **toda** consulta respete la identidad del usuario que
pregunta, **sin depender** de que quien escribe la query "se acuerde" de filtrar?

## Decisión

**Regla de oro:** en la ruta del dashboard, **ninguna consulta se ejecuta sin un filtro
de identidad resuelta**. Se materializa en tres puntos:

### 1. Cadena de resolución de identidad

```
actor_id (Talk)                         p. ej. "users/mmazo"
   └─▶ impersonated_uid (ADR-016)       "mmazo"        (users/<uid> → <uid>)
        └─▶ SELECT id FROM users
            WHERE nc_user_id = <uid>     users.id interno del dashboard
                 └─▶ TODA query filtra por ese id
                     (owner_id / user_id / assigned_to, según la tabla)
```

El `users.id` interno del dashboard es la **clave de identidad** en la BD. Se resuelve
por **`nc_user_id`** (la misma clave estable que usa la impersonation de ADR-016), no
por nombre ni email.

### 2. La identidad es un parámetro **estructural** del puerto, no una convención

`DashboardPort` (ADR-020) recibe la identidad resuelta como **parámetro NO opcional** de
cada método; el adapter **construye internamente** el `WHERE` de identidad en **cada**
query. **No existe** ningún método de "query libre" ni ninguna consulta sin filtro. Así
la identidad **no se puede olvidar**: está en la firma del puerto, igual que
`ActorContext` hace explícito el `uid` en `Skill.execute`. **Prohibido** cualquier
`SELECT` sin filtro de identidad — es un error de diseño, no un descuido tolerable.

### 3. Guests / usuarios sin perfil → la skill rehúsa

- `actor_id` sin `uid` local (guests/federados, ADR-016) ⇒ la skill se **rehúsa** con un
  mensaje claro, **igual que las skills en vivo**. Sin `uid` no hay resolución.
- `uid` que **no existe** en `users` (usuario de Nextcloud sin perfil en el dashboard)
  ⇒ rehúse claro ("no tienes perfil en el dashboard"), **no** una query ambigua ni un
  resultado vacío silencioso.

### Equivalente en BD del `set_user` de ADR-016

Donde las skills en vivo impersonan vía el header `AUTHORIZATION-APP-API` (el servidor
resuelve la identidad), aquí la impersonation se materializa como un **predicado `WHERE`
obligatorio** sobre la identidad resuelta. **Es el mismo principio de ADR-016** —"actúa
solo sobre lo del propio usuario"— trasladado a SQL: el `set_user` de Nextcloud ⇔ el
`WHERE <id_col> = :users_id` del dashboard.

### Datos sensibles (RRHH) — gobernanza

El acceso a **`assessment_evaluations`** (evaluaciones de RRHH) es **sensible**:

- **Alcance inicial: SOLO "lo del propio usuario"** — el usuario ve **sus** evaluaciones,
  filtradas por su `users.id`, y nada más.
- **Acceso a datos de terceros queda FUERA** (p. ej. un **líder** que quiera ver las
  evaluaciones de **su equipo**): requiere un modelo de autorización por rol/jerarquía
  (¿quién puede ver a quién?) que **hoy no existe** y es **pendiente de gobernanza**
  (RRHH + un ADR futuro). Hasta entonces, la regla de oro (solo el propio `users.id`)
  **no se relaja** para RRHH bajo ninguna circunstancia.

## Consecuencias

- **Blast radius = lo del propio usuario.** Por diseño, ninguna consulta puede devolver
  datos de terceros: el puerto **no ofrece** un método sin identidad.
- **Trazabilidad.** Cada consulta lleva la identidad resuelta; se puede loguear el `uid`
  (nunca el contenido sensible) para auditoría.
- **Defensa en profundidad con ADR-022.** Además del `WHERE` obligatorio, el usuario de
  BD **read-only** limita el daño de cualquier bug; y a futuro se puede endurecer con
  **vistas por-usuario** o una capa de autorización.
- **Consistencia con ADR-016.** Un usuario que las skills en vivo rehúsan (guest) también
  lo rehúsa la skill de dashboard: **una sola política de identidad** en todo el bot.
- **RRHH acotado.** El alcance "solo lo mío" desbloquea el caso de uso personal sin abrir
  el flanco de datos de terceros; la ampliación líder→equipo espera gobernanza.

## Alternativas descartadas

- **Confiar en que cada query "recuerde" filtrar** (convención): frágil — un olvido = fuga
  de datos de terceros. Por eso la identidad es **parámetro estructural** del puerto, no
  una convención revisada a ojo.
- **Cuenta de servicio que lee TODO y filtra en Python**: concentra el riesgo en la app;
  un bug de filtrado expone todo el dataset. Se prefiere el filtro **en cada query** +
  usuario de BD de **mínimo privilegio** (ADR-022).
- **Resolver identidad por email o nombre** en vez de `nc_user_id`: frágil (duplicados,
  cambios de nombre). `nc_user_id` es la clave estable que ya ancla la impersonation
  (ADR-016).
- **Confiar en RLS de MySQL**: MySQL no tiene RLS nativo equivalente a Postgres. Se
  documenta como **endurecimiento futuro** vía vistas por-usuario; no se asume disponible.
- **Abrir RRHH a líderes desde el día 1**: sin un modelo de autorización jerárquica
  auditado sería un riesgo de privacidad. Queda **fuera**, pendiente de gobernanza.
