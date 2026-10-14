# Agent graph flow (ParkMind)

Static companion to the interactive pages. Open them in a browser:

- [`agent-graph.html`](agent-graph.html): the animated agent graph. replayable scenarios, step-by-step, with a live state panel.
- [`architecture.html`](architecture.html): the animated architecture stack, with flows, boundaries, implementation status and a "Safety & control" overlay showing which components enforce each control.

Green = implemented, dashed yellow = target / stub.

## 1. Initial planning graph

`build_initial_planning_graph()` compiles the elicitation graph and mounts the planning stage as its `downstream` subgraph. Both share `ParkMindState` and the parent's checkpointer.

The LLM only proposes. A hard constraint does not reach the checker until a person confirms it, and accessibility needs consent plus a `retention_policy` (default `session_only`). The plan is activated only by `interrupt_approval` on the resumed, human-driven path.

```mermaid
flowchart TD
  S([START]) --> EL["elicit<br/>LLM extraction (AnthropicExtractor)"]:::impl
  EL -- missing information --> AM["ask_missing<br/>interrupt()"]:::impl
  AM --> EL
  EL -- new input --> EL
  EL -- constraints proposed --> CH{"confirm_hard_constraints<br/>interrupt(): echo + consent + retention"}:::impl
  CH -- correction with text --> EL
  CH -- empty correction --> AM
  CH -- confirmed --> VA["validate_constraints<br/>only writer of constraints_valid"]:::impl
  VA --> LC

  subgraph PLAN["downstream: planning stage (each node except no_valid_plan and interrupt_approval is _guarded)"]
    LC["load_context<br/>snapshot + weather, reads accessibility by thread_id"]:::impl --> RG["resolve_group<br/>GroupObjective"]:::impl
    RG --> BP["build_plan<br/>resolver + forecast + optimizer + repair loop"]:::impl
    BP --> CK{"check_plan<br/>ConstraintChecker"}:::impl
    CK -- valid --> EX["explain<br/>grounded narrator, else template"]:::impl
    EX --> PR["propose_plan<br/>persist PENDING proposal"]:::impl
    PR --> IN{"interrupt_approval<br/>human decision"}:::impl
    CK -- invalid --> NV["no_valid_plan<br/>explains why, nothing proposed"]:::impl
  end

  IN -- APPROVED --> OK["ResolveProposalUseCase: plan ACTIVE"]:::impl
  IN -- REJECTED --> RJ["REJECTED + rejection_reason"]:::impl
  IN -- EDITED --> ED["ApplyEditUseCase<br/>edit becomes party constraints, proposal EDITED"]:::impl
  ED --> LC
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
- `EDITED` never activates anything: the edit is applied as party constraints, the proposal is resolved as `EDITED` and planning re-enters at `load_context`. If the edit leaves no valid plan, the previous plan is checked again and re-offered (`before_edit_update`).
- `explain` uses a narrator whose numerals must all appear in the plan or check result (grounding), with the stated and learned preferences labelled; anything ungrounded falls back to the deterministic template. English only for now.
- State holds only `accessibility_ref` (guest ids) [C19].

## 2. Replanning graph

`build_replanning_graph()` runs on the thread of the initial plan with `{"thread_id", "events"}` as input. Constraints, profiles, `accessibility_ref` and the group objective come from that thread's checkpoint; the active plan comes from the repository. A replan is a new `plan_id`, checked by the same `ConstraintChecker` as an initial plan (`check_replan`: the whole day, counting what was already walked). Only the resumed approval interrupt activates it; rejecting keeps the current plan.

```mermaid
flowchart TD
  S([START]) --> LP["load_plan<br/>active plan from the repository"]:::impl
  LP -- no plan / not ready --> NP([END: nothing to replan]):::impl
  LP --> HE["handle_events<br/>dedupe · apply execution updates · EventPolicy<br/>supersede the pending proposal if significant"]:::impl
  HE -- report to confirm --> CE{"confirm_events<br/>interrupt(): confirm or dismiss"}:::impl
  CE -- confirmed --> HC["handle_confirmed_events"]:::impl
  CE -- dismissed --> IG
  HE -- nothing significant --> IG["ignore_events<br/>pending proposal is re-opened"]:::impl
  IG -- pending proposal --> IN
  IG -- none --> Z([END])
  HE -- significant --> LC
  HC -- significant --> LC
  HC -- nothing significant --> IG
  LC["load_context"]:::impl --> RG["resolve_group<br/>refresh objective"]:::impl
  RG --> RP["replan<br/>frozen completed stops · remaining walking budget · rest_first"]:::impl
  RP -- no valid replan --> NV([END: nothing proposed]):::impl
  RP --> CK{"check_plan<br/>check_replan"}:::impl
  CK -- invalid --> NV
  CK -- valid --> DF["diff"]:::impl --> EX["explain<br/>grounded narrator, else template"]:::impl --> PR["propose_plan<br/>PENDING proposal, base_plan_id"]:::impl
  PR --> IN{"interrupt_approval<br/>human decision"}:::impl
  IN -- APPROVED --> OK["ResolveProposalUseCase: new plan ACTIVE"]:::impl
  IN -- REJECTED --> RJ["REJECTED: current plan unchanged"]:::impl
  IN -- EDITED --> ED["ApplyEditUseCase<br/>log behavior, edited constraints"]:::impl
  ED --> LC
  IN -- malformed value --> IN
  OK --> Z
  RJ --> Z

  classDef impl fill:#d5e8d4,stroke:#82b366,color:#000
```

Notes:

- `GUEST_FATIGUE` (confirmed) sets the walking cap and makes the new horizon open with a `REST` stop; if it cannot fit, the replan fails closed.
- `EDITED` goes back to `load_context` with the edited constraints: the replan is rebuilt from the active plan and the execution state (a stop already done cannot be removed), checked over the whole day and proposed again. The active plan stays until a proposal is approved.
- Not implemented: REJECT's "update preferences" step; the user-report interpretation front (`INTERPRET`, `SCHEMA VALIDATE`) is upstream of this graph's `events` input.
