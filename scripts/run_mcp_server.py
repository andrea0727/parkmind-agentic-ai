"""Runs parkmind-mcp, the published capability boundary.

    poetry run python scripts/run_mcp_server.py                      # stdio (MCP clients launch it)
    poetry run python scripts/run_mcp_server.py --http --port 8765   # streamable HTTP at /mcp

stdio is what an MCP client such as the LLM-only baseline harness or the
MCP Inspector starts as a subprocess:

    npx @modelcontextprotocol/inspector poetry run python scripts/run_mcp_server.py

HTTP is for long-lived clients (the context loader with
PARKMIND_CONTEXT_TRANSPORT=mcp). The tools read the same Postgres the app uses
(DATABASE_URL), so start it first: docker compose up -d.

HTTP binds to loopback only unless PARKMIND_MCP_TOKEN (32+ characters, no
default) is set: a planner tool given a session id answers with plans that
reflect that session's accessibility requirements. With the token set every
request must send "Authorization: Bearer <token>" (clients read the same
variable), and any --host is allowed. planner.* calls are limited to
PARKMIND_MCP_PLANNER_CALLS_PER_MINUTE (default 30) because they can trigger a
park snapshot capture. On loopback the MCP SDK also checks the Host and Origin
headers (DNS-rebinding protection).

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

from parkmind.tools.mcp_auth import RateLimiter, configured_token, http_bind_error
from parkmind.tools.mcp_server import create_http_app, create_server

DEFAULT_PLANNER_CALLS_PER_MINUTE = 30


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--http", action="store_true", help="serve streamable HTTP instead of stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        token = configured_token()
    except ValueError as exc:
        parser.error(str(exc))
    if args.http and (problem := http_bind_error(args.host, token)):
        parser.error(problem)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if args.http:
        import uvicorn

        limit = int(
            os.environ.get(
                "PARKMIND_MCP_PLANNER_CALLS_PER_MINUTE", DEFAULT_PLANNER_CALLS_PER_MINUTE
            )
        )
        server = create_server(planner_rate_limit=RateLimiter(limit))
        app = create_http_app(server, host=args.host, token=token)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    else:
        create_server().run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
