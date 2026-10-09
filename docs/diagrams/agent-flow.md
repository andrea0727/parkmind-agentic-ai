# Agent graph flow (ParkMind)

Green = implemented, orange = partial (placeholder), dashed yellow = target state.

There are four diagrams: the elicitation graph (already implemented), the initial planning graph **as it is today**, the **target** initial planning flow per §35 of the baseline, and the replanning flow per §36. The order of the current planning graph is provisional: P0-30 replaces it with the §35 order.

## 1. Elicitation (implemented, #78)

The LLM only proposes. A new hard constraint does not reach the checker until a person confirms it, and accessibility requires consent and a `retention_policy` (default `session_only`).

```mermaid
flowchart TD
  S([START]) --> EL["elicit<br/>structured extraction (AnthropicExtractor)"]:::impl
  EL -- missing information --> AM["ask_missing<br/>interrupt()"]:::impl
  AM --> EL
  EL -- constraints extracted --> CH{"confirm_hard_constraints<br/>interrupt(): echo + consent + retention"}:::impl
  CH -- corrects --> EL
  CH -- confirms --> VA["validate_constraints<br/>only node that writes constraints_valid=True"]:::impl
  VA --> DS["downstream<br/>placeholder: load_context hooks in here [P0-30]"]:::partial
  DS --> Z([END])

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
  classDef partial fill:#ffe6cc,stroke:#d79b00,color:#000
```

## 2. Initial planning: current state

This graph is not yet connected to the elicitation graph. Its order is provisional.

```mermaid
flowchart TD
  S([START]) --> A["resolve_preferences<br/>placeholder average"]:::partial
  A --> B["fetch_context<br/>LiveContext without waits"]:::partial
  B --> C["synthesize_plan<br/>empty placeholder plan"]:::partial
  C --> D["propose_plan<br/>PENDING Proposal (checker gate pending)"]:::partial
  D --> E{"interrupt_approval<br/>human decision"}:::impl
  E -- approve --> F["ACTIVE Plan"]:::impl
  E -- reject --> G["REJECTED Proposal"]:::impl
  F --> Z([END])
  G --> Z

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
  classDef partial fill:#ffe6cc,stroke:#d79b00,color:#000
```

## 3. Initial planning: target (§35)

`RESOLVE GROUP OBJECTIVE` comes **after** `LOAD CONTEXT` because it needs `accessibility_results` (C23). Today `resolve_preferences` runs first; reordering it is part of P0-30.

```mermaid
flowchart TD
  S([START]) --> EL["ELICIT"]:::impl
  EL --> CH{"CONFIRM HARD CONSTRAINTS"}:::impl
  CH --> VA["VALIDATE CONSTRAINTS"]:::impl
  VA --> LC["LOAD CONTEXT<br/>snapshot + accessibility_results"]:::target
  LC --> GO["RESOLVE GROUP OBJECTIVE"]:::target
  GO --> SO["SYNTHESIZE / SOLVE<br/>BuildPlan: resolver + forecast + optimizer"]:::target
  SO --> CK{"CHECK<br/>ConstraintChecker"}:::target
  CK -- violates rules --> RS["RE-SOLVE [P0-21]"]:::target
  RS --> CK
  CK -- valid --> EX["EXPLAIN<br/>Concierge LLM"]:::target
  EX --> PR["PROPOSE<br/>PENDING Proposal"]:::target
  PR --> IN{"INTERRUPT<br/>human decision"}:::impl
  IN -- approve --> OK["ACTIVE Plan"]:::impl
  IN -- reject --> RJ["REJECTED Proposal"]:::impl
  IN -- edit --> ED["EDIT<br/>human adjustment"]:::target
  ED --> CK
  OK --> Z([END])
  RJ --> Z

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
  classDef target fill:#fff2cc,stroke:#d6b656,stroke-dasharray:5 5,color:#000
```

## 4. Replanning (§36, target)

Today `replanning_graph.py` only has the docstring describing the flow and raises `NotImplementedError`. `RESOLVE GROUP OBJECTIVE` runs again with fresh context, and `RE-SOLVE` is part of the contract.

```mermaid
flowchart TD
  EV[["Event: wait time, weather, closure"]]:::target --> EP["EVENT POLICY"]:::target
  EP --> LC["LOAD CONTEXT<br/>fresh context"]:::target
  LC --> GO["RESOLVE GROUP OBJECTIVE<br/>re-run"]:::target
  GO --> RP["REPLAN"]:::target
  RP --> CP{"CHECK PLAN"}:::target
  CP -- violates rules --> RS["RE-SOLVE [P0-21]"]:::target
  RS --> CP
  CP -- valid --> PD["PLAN DIFF"]:::impl
  PD --> EX["EXPLAIN<br/>Concierge LLM"]:::target
  EX --> PR["PROPOSE"]:::target
  PR --> IN{"INTERRUPT<br/>human decision"}:::impl

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
  classDef target fill:#fff2cc,stroke:#d6b656,stroke-dasharray:5 5,color:#000
```

`PLAN DIFF` is green because `diff_plans` already exists; the graph node that calls it is still pending.
