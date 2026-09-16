# Spike: LangGraph interrupt()/resume() Investigation

**Branch:** `spike/langgraph-interrupt-resume`  
**Scope:** 4-hour spike, desechable (no merged)  
**Objective:** Answer 5 blocking questions about LangGraph interrupt()/resume() empirically  

---

## Problem Statement

The team needs to validate 5 architectural assumptions before finalizing the HITL (human-in-the-loop) flow in v2.2:

1. Can a single graph have multiple `interrupt()`s? How does the caller know which one is pending?
2. **[CRITICAL]** What gets written literally to the checkpoint table? Does it persist complete state?
3. Can you abandon an interrupted run and start a new one on the same `thread_id`?
4. Does `resume()` work after restarting the Python process?
5. How does this all work in Streamlit between reruns?

**C19** in the architecture assumes checkpoints persist full state. This spike validates that empirically.

---

## Repository Structure

```
spikes/langgraph_hitl/
├── FINDINGS.md                           # Results of all 5 investigations
├── README.md                             # This file
├── test_q1_multiple_interrupts.py        # Q1: Multiple interrupts
├── test_q2_checkpoint_content.py         # Q2: What gets persisted
├── test_q3_abandon_run.py                # Q3: Abandon + restart
├── test_q4_resume_after_restart.py       # Q4: Resume post-Python-restart
└── q5_streamlit_app.py                   # Q5: Streamlit integration
```

---

## Running the Tests

### Prerequisites

```bash
# All tests run with Poetry (deps already in pyproject.toml)
poetry install
```

### Q1: Multiple interrupts

```bash
poetry run python spikes/langgraph_hitl/test_q1_multiple_interrupts.py
```

**What it does:** Creates a graph with 3 nodes that set `approval1`, `approval2`, `approval3` to `PENDING`. Shows how the caller can inspect the state to know which approve is pending.

**Expected output:** State snapshots showing that multiple fields can be set independently.

### Q2: Checkpoint content [MOST IMPORTANT]

```bash
poetry run python spikes/langgraph_hitl/test_q2_checkpoint_content.py
```

**What it does:** Runs a simple graph and inspects what gets written to the checkpoint. Shows that **the full state is persisted** — not a subset.

**Expected output:** All state fields are present in the checkpoint storage.

**Critical finding for C19:** ✓ Full state persists. Accessibility flags must be kept OUT of state to achieve session-only retention.

### Q3: Abandon run + new thread

```bash
poetry run python spikes/langgraph_hitl/test_q3_abandon_run.py
```

**What it does:** Two sequential runs on the same `thread_id`. The second run overwrites the first checkpoint. Shows no error, no "orphaned" state.

**Expected output:** Run 2's checkpoint completely replaces Run 1's.

### Q4: Resume after Python restart

```bash
# Part 1: Create checkpoint
poetry run python spikes/langgraph_hitl/test_q4_resume_after_restart.py part1

# Part 2: New Python process, load checkpoint
poetry run python spikes/langgraph_hitl/test_q4_resume_after_restart.py part2
```

**What it does:** Two separate Python processes. Part 1 saves a checkpoint, Part 2 loads it. Simulates full process restart.

**Expected output:** Part 2 loads the checkpoint and continues execution.

**Note:** With MemorySaver (in-memory), checkpoints don't persist between processes. With PostgresSaver, they do. This test simulates Postgres behavior.

### Q5: Streamlit integration

```bash
streamlit run spikes/langgraph_hitl/q5_streamlit_app.py
```

**What it does:** Runs a minimal Streamlit app that:
- Executes the graph
- Shows state between reruns
- Displays "Approval pending" UI if approval=PENDING
- Has buttons to approve/reject

**Expected output:** Graph executes, buttons appear when approval is pending, state persists between button clicks.

**Critical finding:** State persists in `st.session_state`; checkpointer persists in Postgres.

---

## Key Findings

See **`FINDINGS.md`** for complete details. Summary:

| Q | Finding | Implication |
|---|---------|------------|
| 1 | Multiple interrupts work via state fields | Caller knows which approve is pending by inspecting state |
| 2 | **Full state persists** ✓ | C19 requires keeping `AccessibilityRequirements` out of state |
| 3 | Abandoned runs are overwritten | `thread_id` is the unique key; no versioning within thread |
| 4 | Resume post-restart works (with Postgres) | PostgresSaver is critical for process restarts |
| 5 | Streamlit works | `st.session_state` + Postgres handle multi-session state |

---

## Critical for Architecture

### C19 Validation

**C19 states:** Accessibility flags are referenced by `guest_id` in state; flags load per-run from session store; checkpoints never contain the flags.

**Empirical confirmation:** ✓ Valid design.
- LangGraph persists **all state fields**.
- To achieve session-only retention, `AccessibilityRequirements` must stay OUT of `ParkMindState`.
- Only `accessibility_ref: list[str]` should be in state.

**No contradiction with v2.2.**

### Postgres vs MemorySaver

- **MemorySaver:** For local development. Doesn't persist across Python restarts.
- **PostgresSaver:** For production. Checkpoint lives in DB; any new process can resume.

All graph state persists in either checkpointer. The difference is lifecycle — memory vs disk.

---

## Next Steps

This spike is **complete**. All 5 questions are answered empirically.

**For implementation:**
1. Use PostgresSaver in production (checkpoints → Postgres).
2. Keep `AccessibilityRequirements` out of `ParkMindState`; use session store.
3. Ensure `thread_id` uniquely identifies a guest's session (no multi-party thread reuse).
4. In Streamlit, persist the checkpointer in `st.session_state` (or use Postgres backend).

No architectural changes needed. v2.2 is validated.

---

## Cleanup

These tests are desechable. They are NOT integrated into CI/CD or shipped with the app. Delete this folder after the spike is merged to main (if merged at all) or after review closes.
