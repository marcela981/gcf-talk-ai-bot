# ADR-023 — Estrategia de fuente autoritativa: dashboard_db vs Nextcloud en vivo

| | |
|--|--|
| **Estado** | **Propuesto** (2026-07-02) |
| **Iteración** | Integración con la BD del dashboard corporativo (Bloque 3) |
| **Relacionados** | ADR-020 (`DashboardPort` como fuente de contexto), ADR-021 (identidad), ADR-016 (impersonation en vivo), Bloques 2.2/2.3/2.4 (Calendar/Deck/Files en vivo, OCS/WebDAV/CalDAV), ADR-006-ter (RAG), ADR-018 (descripciones de skill enrutan al LLM) |

## Problema

Con `DashboardPort` (ADR-020) el bot pasa a tener **dos fuentes que hablan de lo mismo**
para algunas preguntas:

- **Deck en vivo** vía OCS impersonado (skill `consultar_deck`, Bloque 2.3): estado
  **fresco** del board bajo la identidad del usuario.
- **`tasks` / `deck_*` de `dashboard_db`** (espejo/derivado, ADR-020): puede ir **con
  retraso** respecto al board en vivo.

Además hay datos que **solo** están en el dashboard (**horas, desempeño, evaluaciones**)
y datos que **solo** están frescos en Nextcloud (**estado actual** del board, eventos de
calendario, archivos). Si dos fuentes responden la **misma** pregunta con datos
**distintos**, el bot daría **respuestas contradictorias**. ¿Qué fuente responde qué?

## Decisión

Regla de **fuente autoritativa por tipo de pregunta**, explícita:

### 1. Datos "de dashboard" (agregados / derivados / propios) → `dashboard_db`

"**mis** tareas del dashboard", "**mis horas**", "**mi desempeño**", "**mis
evaluaciones**", reportes e **histórico** → **la BD es autoritativa** (es donde viven esos
datos; Nextcloud ni los tiene).

### 2. Estado "**ahora**" de Nextcloud → skills en vivo impersonadas

"¿qué hay en el board Deck **ahora**?", "estado **actual** de la columna X", "mis
**eventos** de calendario", "mis **archivos**" → **las skills en vivo** (OCS/WebDAV/CalDAV,
ADR-016) son autoritativas por **frescura**; el espejo del dashboard puede ir atrasado.

### 3. Solape (tasks/deck_* del dashboard vs Deck en vivo) → gana el tipo de pregunta

Cuando una pregunta pueda ir a ambas:

- "**estado / ahora / actual**" ⇒ **OCS en vivo**.
- "**reporte / histórico / agregado / desempeño / cuántas**" ⇒ **`dashboard_db`**.
- Si el usuario **lo pide explícito** ("según el dashboard" ⇒ BD; "según Deck / ahora" ⇒
  OCS), se respeta su elección.

### 4. No mezclar en una skill sin regla

Una misma skill **no** debe combinar **silenciosamente** ambas fuentes. Dos opciones,
en orden de preferencia:

1. **Skills separadas** (preferida, SRP — como 2.2/2.3): `consultar_deck` (en vivo) y
   `consultar_dashboard` (BD) son tools distintas; el **LLM enruta por la `description`**
   (ADR-018), que deja claro qué cubre cada una ("estado actual del tablero" vs
   "reportes/horas/desempeño/histórico").
2. Si **una** skill necesitara consultar ambas, **debe etiquetar la fuente** de cada dato
   en la respuesta y aplicar la precedencia de §3 (frescura para estado, BD para
   agregados) — nunca fusionar sin marca.

### 5. La respuesta cita la fuente cuando haya riesgo de confusión

Ante datos potencialmente desfasados, la respuesta puede **anclar la fuente y el corte**:
"según el dashboard, al corte de <fecha>" vs "en Deck ahora mismo".

## Consecuencias

- **Sin contradicciones:** cada pregunta tiene **una** fuente autoritativa definida.
- **Frescura vs agregación explícitas:** "ahora" ⇒ Nextcloud en vivo; "reporte/histórico"
  ⇒ dashboard. El usuario y el LLM saben qué esperar.
- **Enrutado limpio por descripción** (ADR-018): las `description` de las skills fijan la
  frontera; el LLM elige la tool correcta sin heurística frágil.
- **Trazabilidad:** citar fuente/corte hace la respuesta **auditable**.
- **Deuda:** el espejo `deck_*` del dashboard puede **desincronizarse**; se documenta que
  **no** es autoritativo para "estado ahora". Si genera confusión recurrente, evaluar **no
  exponer** `deck_*` del lado bot y servir Deck **solo** por OCS en vivo.

## Alternativas descartadas

- **Una skill que mezcla ambas fuentes "lo mejor posible"**: produce contradicciones y
  respuestas **no auditables**; se **prohíbe** mezclar sin regla (§4).
- **Dashboard como única fuente** (dejar de usar OCS en vivo): pierde **frescura** (el
  espejo va atrasado) y contradice ADR-020 (las skills en vivo siguen autoritativas para
  datos frescos de Nextcloud).
- **OCS en vivo como única fuente** (ignorar el dashboard): **imposible** para
  horas/desempeño/evaluaciones — no existen en Nextcloud. Por eso ambas coexisten **con
  regla**.
- **Que el LLM decida la fuente sin regla explícita**: no determinista y propenso a
  contradicciones; la frontera se fija en las `description` y en esta regla, no se deja al
  azar del prompt.
