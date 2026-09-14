"""
Registers data_tools / knowledge_tools / planner_tools as a single
in-process MCP server, namespaced (data.*, knowledge.*, planner.*).

Physical separation into multiple MCP servers is documented as a future
option (see the wiki) but out of scope for month 1 — one server, one
process, namespaced tools.
"""


def create_server():
    raise NotImplementedError
