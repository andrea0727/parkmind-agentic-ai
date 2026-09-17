"""
Replanning state machine.

EVENT -> EVENT POLICY -> LOAD CONTEXT -> REPLAN -> CHECK PLAN -> PLAN DIFF
-> EXPLAIN -> PROPOSE -> INTERRUPT -> HUMAN DECISION
"""



def build_replanning_graph():
    raise NotImplementedError


# graph = build_replanning_graph()   # uncomment once implemented
