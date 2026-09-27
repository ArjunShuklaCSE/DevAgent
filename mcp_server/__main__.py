"""``python -m mcp_server``: serve DevAgent over MCP (stdio by default)."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from mcp.server.stdio import stdio_server
from pydantic import SecretStr

from backend.logging_setup import configure_logging
from mcp_server.api_client import DevAgentApi
from mcp_server.server import DevAgentMcp


async def _serve(app: DevAgentMcp, api: DevAgentApi | None) -> None:
    server = app.server()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        if api is not None:
            await api.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devagent-mcp", description=__doc__)
    parser.add_argument("--root", type=Path, help="local checkout the read-only code tools work on")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("DEVAGENT_MCP_API_URL", "http://localhost:8000"),
        help="DevAgent API for run control (env DEVAGENT_MCP_API_URL)",
    )
    parser.add_argument("--no-runs", action="store_true", help="expose only the code tools")
    args = parser.parse_args(argv)
    if args.root is not None and not args.root.is_dir():
        parser.error(f"--root {args.root} is not a directory")
    if args.root is not None and not (args.root / ".git").exists():
        parser.error(f"--root {args.root} must be a git checkout (search lists files via git)")

    # Logs go to stderr: stdout is the MCP wire.
    configure_logging(os.environ.get("DEVAGENT_LOG_LEVEL", "INFO"), "console")
    cookie = os.environ.get("DEVAGENT_MCP_SESSION")
    api = (
        None
        if args.no_runs
        else DevAgentApi(args.api_url, session_cookie=SecretStr(cookie) if cookie else None)
    )
    asyncio.run(_serve(DevAgentMcp(api, args.root), api))
    return 0


if __name__ == "__main__":
    sys.exit(main())
