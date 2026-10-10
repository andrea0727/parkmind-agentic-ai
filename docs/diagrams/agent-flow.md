# Agent graph flow (ParkMind)

Static companion to the interactive pages. Open them in a browser:

- [`agent-graph.html`](agent-graph.html): the animated agent graph. replayable scenarios, step-by-step, with a live state panel.
- [`architecture.html`](architecture.html): the animated architecture stack, with flows, boundaries, implementation status and a "Safety & control" overlay showing which components enforce each control.

Green = implemented, dashed yellow = target / stub.

## 1. Initial planning graph (implemented: P0-29 + P0-30)

`build_initial_planning_graph()` compiles the elicitation graph and mounts the planning stage as its `downstream` subgraph. Both share `ParkMindState` and the parent's checkpointer.

The LLM only proposes. A hard constraint does not reach the checker until a person confirms it, and accessibility needs consent plus a `retention_policy` (default `session_only`). The plan is activated only by `interrupt_approval` on the resumed, human-driven path.

```mermaid
flowchart TD
  S([START]) --> EL["elicit<br/>LLM extraction (AnthropicExtractor)"]:::impl
  EL -- missing information --> AM["ask_missing<br/>interrupt()"]:::impl
  AM --> EL
  EL -- constraints proposed --> CH{"confirm_hard_constraints<br/>interrupt(): echo + consent + retention"}:::impl
  CH -- corrects --> EL
  CH -- confirmed --> VA["validate_constraints<br/>only writer of constraints_valid"]:::impl
  VA --> LC

  subgraph PLAN["downstream: planning stage (each node except no_valid_plan and interrupt_approval is _guarded)"]
    LC["load_context<br/>snapshot + weather, reads accessibility by thread_id"]:::impl --> RG["resolve_group<br/>GroupObjective"]:::impl
    RG --> BP["build_plan<br/>resolver + forecast + optimizer + repair loop"]:::impl
    BP --> CK{"check_plan<br/>ConstraintChecker"}:::impl
    CK -- valid --> EX["explain"]:::impl
    EX --> PR["propose_plan<br/>persist PENDING proposal"]:::impl
    PR --> IN{"interrupt_approval<br/>human decision"}:::impl
    CK -- invalid --> NV["no_valid_plan<br/>explains why, nothing proposed"]:::impl
  end

  IN -- APPROVED --> OK["ResolveProposalUseCase: plan ACTIVE"]:::impl
  IN -- REJECTED --> RJ["REJECTED + rejection_reason"]:::impl
  IN -- malformed value --> IN
  OK --> Z([END])
  RJ --> Z
  NV --> Z
  UN["PlanningUnavailableError<br/>plain-language message"]:::err -.-> Z

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
  classDef err fill:#f8cecc,stroke:#b85450,color:#000
```

Notes:

- `propose_plan` is reachable only through the valid edge of `check_plan` and re-asserts `check_result.valid`.
- Nothing before an `interrupt()` has a side effect: LangGraph re-runs a node from the top on resume.
- `EDITED` decisions are rejected with an error until P0-32.
- State holds only `accessibility_ref` (guest ids) [C19].

## 2. Replanning (target, §36)

`replanning_graph.py` only has the docstring and `build_replanning_graph()` raises `NotImplementedError`. `diff_plans` exists; the node that calls it does not.

```mermaid
flowchart TD
  EV[["Event: wait time, weather, closure"]]:::target --> EP["EVENT POLICY"]:::target
  EP --> LC["LOAD CONTEXT"]:::target
  LC --> RP["REPLAN"]:::target
  RP --> CP{"CHECK PLAN"}:::target
  CP -- valid --> PD["PLAN DIFF"]:::target
  PD --> EX["EXPLAIN"]:::target
  EX --> PR["PROPOSE"]:::target
  PR --> IN{"INTERRUPT<br/>human decision"}:::target

  classDef target fill:#fff2cc,stroke:#d6b656,stroke-dasharray:5 5,color:#000
```
