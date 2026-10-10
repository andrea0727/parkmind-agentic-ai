"""Runs parkmind-mcp, the published capability boundary (backlog P0-24).

    poetry run python scripts/run_mcp_server.py                      # stdio (MCP clients launch it)
    poetry run python scripts/run_mcp_server.py --http --port 8765   # streamable HTTP at /mcp

stdio is what an MCP client such as the LLM-only baseline harness (P1-15) or the
MCP Inspector starts as a subprocess:

    npx @modelcontextprotocol/inspector poetry run python scripts/run_mcp_server.py

HTTP is for long-lived clients (the context loader with
PARKMIND_CONTEXT_TRANSPORT=mcp). The tools read the same Postgres the app uses
(DATABASE_URL), so start it first: docker compose up -d.

On stdio, stdout is the protocol channel: everything this process logs goes to
stderr.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
# The server is the capability boundary: it reads its own data in-process and
# never through itself over MCP, whatever .env says (set before settings load).
os.environ["PARKMIND_CONTEXT_TRANSPORT"] = "in_process"

from parkmind.tools.mcp_server import create_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--http", action="store_true", help="serve streamable HTTP instead of stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    server = create_server()
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
