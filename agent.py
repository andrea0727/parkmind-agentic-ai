"""
Root entrypoint — this is the file langgraph.json points to, and the file
you run locally to sanity-check the graphs compile.

Standard LangGraph project convention: this file builds and exposes the
compiled graph(s) as module-level variables. Everything else (state, nodes,
tools, business logic) lives in src/parkmind/ — this file just wires it
together at the top level.
"""

import sys
import os

# Make src/ importable without installing the package (no pyproject.toml
# ceremony needed for a project this size — see README "Getting started").
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from parkmind.graph.initial_planning_graph import build_initial_planning_graph
from parkmind.graph.replanning_graph import build_replanning_graph

# graph = build_initial_planning_graph()
# replanning_graph = build_replanning_graph()

if __name__ == "__main__":
    print("TODO: build_initial_planning_graph() and build_replanning_graph() "
          "are still stubs — see src/parkmind/graph/")
