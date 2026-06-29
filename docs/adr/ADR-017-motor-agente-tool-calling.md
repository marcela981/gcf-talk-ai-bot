# ADR-017 — Motor de agente por _tool-calling_ (router = LLM, no regex)

| | |
|--|--|
| **Estado** | **Propuesto** (2026-06-29) |
| **Iteración** | Motor de agente / skills (sucesora de la Fase 2 RAG) |
| **Relacionados** | ADR-002 (`LLMPort` + Adapter), ADR-003 (stateless), ADR-004 (trigger por @mención), ADR-014 (memoria por sala), ADR-016 (identidad), ADR-018 (contrato de Skill), D1 (fragilidad del regex) |

## Problema

Hoy `ConversationService` decide responder por **@mención** (ADR-004) y produce
**sólo texto** vía `LLMPort.complete` (`app/services/llm_port.py:9-15`). Para
resolver **acciones** ("créame una tarea", "busca el contrato X y resúmelo")
hace falta un **router** que decida **qué** capacidad invocar y **con qué
argumentos**.

¿El router es **reglas/regex** sobre el texto del mensaje, o el **propio LLM**
mediante _function/tool-calling_?

## Decisión

**El router es el LLM, por _tool-calling_.** Las skills (ADR-018) se exponen al
modelo como herramientas con su **JSON-schema** (`name`, `description`,
`parameters_schema`); el modelo **elige** la tool y emite los argumentos como
**JSON validado** contra el schema. **No se usa regex / _keyword-matching_ para
enrutar.**

### Extensión del puerto: aditiva (OCP)

Se añade a `LLMPort` un método nuevo —descrito en prosa, no implementado aquí—
`chat_with_tools(messages, tools, *, model=None)` **sin tocar** `complete`, que
sigue siendo la ruta de texto puro de Fase 1/2. Esto respeta **OCP** y ADR-002:

- Los adapters que **no** soporten tools simplemente no implementan el método
  nuevo (o lo declaran no soportado); su ruta `complete` sigue intacta.
- La firma exacta del retorno (tool-calls solicitadas **vs.** texto final) se fija
  en el diseño detallado; debe modelar dos resultados: **(a)** el modelo pide _N_
  tool-calls, **(b)** el modelo responde texto final.

### _Tool-use loop_ dentro del webhook (stateless preservado)

El bucle agente vive **dentro del manejo de un único webhook** `/talk_bot`
(`app/main.py:195`). Por iteración:

1. El servicio llama `chat_with_tools(messages, tools)`.
2. Si el modelo pide tool-calls → ejecuta cada skill (ADR-018) **bajo la
   identidad impersonada** (ADR-016), anexa los resultados como turnos de
   herramienta al arreglo de mensajes **en memoria local**, y **reitera**.
3. Cuando el modelo devuelve **texto final**, ese texto es el `reply`.

Todo el estado del loop vive en **variables locales del request**: nada se
persiste entre webhooks (consistente con ADR-003). La **memoria conversacional
por sala** (ADR-014) sigue siendo el único estado entre requests y **no cambia**.

### Salvaguardas

- **Tope de iteraciones** del loop: evita bucles infinitos de tool-calling y
  acota coste/latencia (refuerza el espíritu de ADR-004 y la deuda **D3**). Al
  alcanzar el tope, se cierra con el mejor texto disponible o un _fallback_.
- **Gate previo intacto**: el agente sólo arranca **tras `should_reply`**
  (ADR-004) y el anti-loop de bots (ADR-014). La detección de mención no se
  toca.

## Consecuencias

- **El modelo, no nosotros, traduce lenguaje natural → intención.** Añadir una
  capacidad **no toca el router** (OCP): basta **registrar la skill** (ADR-018).
- **Ruta de Fase 1/2 preservada.** `complete` y el RAG-solo-texto siguen
  intactos; un despliegue puede habilitar tool-calling o no.
- **Coste/latencia suben** (varias llamadas al LLM por turno). Mitigado por el
  tope de iteraciones y porque el gate de @mención limita la frecuencia.
- **Dependencia de un modelo con _tool-calling_ fiable.** El adapter OpenAI ya lo
  permite; los modelos on-prem (**D5**) deberán soportarlo o quedarse en modo
  texto (`complete`).

## Alternativas descartadas

- **Router por regex / keywords.** Frágil — la propia detección de mención por
  regex ya está registrada como deuda **D1**; un matching así no escala a _N_
  skills, no extrae argumentos estructurados y reimplementa mal lo que el LLM
  hace de forma nativa.
- **Clasificación "a mano" con un LLM** (una llamada que devuelve el nombre de la
  skill en texto y luego parseás): es _tool-calling_ reinventado **sin** el
  JSON-schema ni la validación de argumentos que da la API nativa.
- **Orquestador con estado persistente** (cola, máquina de estados en BD): YAGNI
  y rompe el stateless de ADR-003 sin necesidad, mientras el loop cierre dentro
  del webhook.
