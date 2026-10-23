"""MCP server exposing the Panic Index research tools.

Run over stdio (what Claude Code, Claude Desktop, and Cursor launch):
    python mcp_server.py

Or over Streamable HTTP:
    python mcp_server.py --http --port 8765
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from tools import RESEARCH_TOOLS, get_store  # noqa: E402

INSTRUCTIONS = """\
Point-in-time market-regime research over the Composite Panic Index
(VIX + CNN Fear & Greed) for QQQ, SMH, SPX, and DJI, plus FOMC statements and
minutes. Every tool requires an as_of date and reads nothing published after
it; pass the date the question is being asked from, not today's date, when
studying history. Results include data_through so you can cite how fresh the
data is. Treat outputs as historical research evidence, not trading advice.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True,
                            destructive_hint=False, open_world_hint=False)


def build_server():
    store = get_store()
    server = MCPServer(
        name="panic-index-research",
        title="Panic Index Research",
        instructions=INSTRUCTIONS + (
            f"Market data covers {store.first_date} to {store.last_date}."
        ),
        version="1.0.0",
    )
    for fn in RESEARCH_TOOLS:
        server.add_tool(fn, annotations=READ_ONLY, structured_output=True)
    return server


server = build_server()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", action="store_true", help="Serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.http:
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run()


if __name__ == "__main__":
    main()
