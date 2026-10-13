"""
Root entrypoint — this is the file langgraph.json points to, and the file
you run locally to sanity-check the graphs compile.

Standard LangGraph project convention: this file builds and exposes the
compiled graph(s) as module-level variables. Everything else (state, nodes,
tools, business logic) lives in src/parkmind/ — this file just wires it
together at the top level.
"""

import os
import sys

# Make src/ importable without installing the package (no pyproject.toml
# ceremony needed for a project this size — see README "Getting started").
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.initial_planning_graph import build_initial_planning_graph
from parkmind.graph.replanning_graph import build_replanning_graph

# The replanning graph runs on the thread of the initial plan and reads its checkpoint
# (constraints, accessibility_ref, group objective), so both share one checkpointer.
_checkpointer = default_checkpointer()
graph = build_initial_planning_graph(checkpointer=_checkpointer)
replanning_graph = build_replanning_graph(_checkpointer)

if __name__ == "__main__":
    for name, compiled in (("initial_planning", graph), ("replanning", replanning_graph)):
        nodes = [n for n in compiled.get_graph().nodes if not n.startswith("__")]
        print(f"{name}: {len(nodes)} nodes compiled")
