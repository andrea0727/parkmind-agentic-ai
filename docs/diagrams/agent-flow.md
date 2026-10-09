# Flujo del grafo de agentes (ParkMind)

Verde = implementado, naranja = parcial (placeholder), amarillo punteado = estado objetivo.

Hay tres diagramas: el grafo de elicitación (ya implementado), el grafo de planificación **tal como está hoy** y el flujo **objetivo** según §35 del baseline. El orden del grafo de planificación actual es provisional: P0-30 lo reemplaza por el orden de §35.

## 1. Elicitación (implementado, #78)

El LLM solo propone. Una restricción dura nueva no llega al checker sin que una persona la confirme, y la accesibilidad exige consentimiento y `retention_policy` (por defecto `session_only`).

```mermaid
flowchart TD
  S([START]) --> EL["elicit<br/>extracción estructurada (AnthropicExtractor)"]:::impl
  EL -- falta información --> AM["ask_missing<br/>interrupt()"]:::impl
  AM --> EL
  EL -- restricciones extraídas --> CH{"confirm_hard_constraints<br/>interrupt(): eco + consentimiento + retención"}:::impl
  CH -- corrige --> EL
  CH -- confirma --> VA["validate_constraints<br/>único que escribe constraints_valid=True"]:::impl
  VA --> DS["downstream<br/>placeholder: aquí se engancha load_context [P0-30]"]:::partial
  DS --> Z([END])

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
  classDef partial fill:#ffe6cc,stroke:#d79b00,color:#000
```

## 2. Planificación inicial: estado actual

Este grafo todavía no está conectado al de elicitación. Su orden es provisional.

```mermaid
flowchart TD
  S([START]) --> A["resolve_preferences<br/>promedio placeholder"]:::partial
  A --> B["fetch_context<br/>LiveContext sin esperas"]:::partial
  B --> C["synthesize_plan<br/>plan vacío placeholder"]:::partial
  C --> D["propose_plan<br/>Proposal PENDING (gate del checker pendiente)"]:::partial
  D --> E{"interrupt_approval<br/>decisión humana"}:::impl
  E -- aprobar --> F["Plan ACTIVO"]:::impl
  E -- rechazar --> G["Proposal RECHAZADA"]:::impl
  F --> Z([END])
  G --> Z

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
  classDef partial fill:#ffe6cc,stroke:#d79b00,color:#000
```

## 3. Planificación inicial: objetivo (§35)

`RESOLVE GROUP OBJECTIVE` va **después** de `LOAD CONTEXT` porque necesita `accessibility_results` (C23). Hoy `resolve_preferences` va primero; reordenarlo es parte de P0-30.

```mermaid
flowchart TD
  S([START]) --> EL["ELICIT"]:::impl
  EL --> CH{"CONFIRM HARD CONSTRAINTS"}:::impl
  CH --> VA["VALIDATE CONSTRAINTS"]:::impl
  VA --> LC["LOAD CONTEXT<br/>snapshot + accessibility_results"]:::target
  LC --> GO["RESOLVE GROUP OBJECTIVE"]:::target
  GO --> SO["SYNTHESIZE / SOLVE<br/>BuildPlan: resolver + forecast + optimizer"]:::target
  SO --> CK{"CHECK<br/>ConstraintChecker"}:::target
  CK -- viola reglas --> RS["RE-SOLVE [P0-21]"]:::target
  RS --> CK
  CK -- válido --> EX["EXPLAIN<br/>Concierge LLM"]:::target
  EX --> PR["PROPOSE<br/>Proposal PENDING"]:::target
  PR --> IN{"INTERRUPT<br/>decisión humana"}:::impl
  IN -- aprobar --> OK["Plan ACTIVO"]:::impl
  IN -- rechazar --> RJ["Proposal RECHAZADA"]:::impl
  IN -- editar --> ED["EDIT<br/>ajuste humano"]:::target
  ED --> CK
  OK --> Z([END])
  RJ --> Z

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
  classDef target fill:#fff2cc,stroke:#d6b656,stroke-dasharray:5 5,color:#000
```

## 4. Replanning (§36, objetivo)

Hoy `replanning_graph.py` solo tiene el docstring con el flujo y lanza `NotImplementedError`. `RESOLVE GROUP OBJECTIVE` se vuelve a ejecutar con el contexto fresco, y `RE-SOLVE` forma parte del contrato.

```mermaid
flowchart TD
  EV[["Evento: espera, clima, cierre"]]:::target --> EP["EVENT POLICY"]:::target
  EP --> LC["LOAD CONTEXT<br/>contexto fresco"]:::target
  LC --> GO["RESOLVE GROUP OBJECTIVE<br/>re-ejecutado"]:::target
  GO --> RP["REPLAN"]:::target
  RP --> CP{"CHECK PLAN"}:::target
  CP -- viola reglas --> RS["RE-SOLVE [P0-21]"]:::target
  RS --> CP
  CP -- válido --> PD["PLAN DIFF"]:::impl
  PD --> EX["EXPLAIN<br/>Concierge LLM"]:::target
  EX --> PR["PROPOSE"]:::target
  PR --> IN{"INTERRUPT<br/>decisión humana"}:::impl

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
  classDef target fill:#fff2cc,stroke:#d6b656,stroke-dasharray:5 5,color:#000
```

`PLAN DIFF` aparece en verde porque `diff_plans` ya existe; el nodo del grafo que lo invoca sigue pendiente.
