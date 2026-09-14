"""
Initial planning state machine.

START -> ELICIT -> VALIDATE CONSTRAINTS -> RESOLVE GROUP PREFERENCES ->
LOAD CONTEXT -> BUILD PLAN -> CHECK PLAN -> EXPLAIN -> PROPOSE ->
INTERRUPT -> HUMAN DECISION -> (APPROVE | EDIT | REJECT)

Exposes a module-level compiled `graph` once built, so agent.py (root) and
langgraph.json can reference it directly — that's the standard LangGraph
project convention.
"""

from parkmind.graph.state import ParkMindState


def build_initial_planning_graph():
    raise NotImplementedError


# graph = build_initial_planning_graph()   # uncomment once implemented
