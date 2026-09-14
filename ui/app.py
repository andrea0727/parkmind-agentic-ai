"""
Streamlit entrypoint. Calls the agent graphs directly, in-process — no API
layer this month.

TODO: chat input -> parkmind.graph.initial_planning_graph -> render
Proposal as a card with Approve / Edit / Reject buttons -> on decision,
resume the graph via LangGraph's checkpointer.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import streamlit as st

st.title("ParkMind")
st.write("TODO: wire this up to parkmind.graph.*")
