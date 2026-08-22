"""FastMCP server for lock-aware Calibre plugin install/restart.

Cursor stdio::

    python -m calibre_dev

Optional shared HTTP coordinator (one process for every agent)::

    python -m calibre_dev --http --port 8765
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

from calibre_dev.calibre import CalibreCtl

log = logging.getLogger("calibre_dev")

INSTRUCTIONS = """
ao3kit Calibre plugin reload helper. Host-wide lock: only one restart at a time.

When to use:
- Always install the plugin after repo changes.
- Do NOT restart Calibre on every change.
- Restart only when you must load the new zip in the GUI during this session
  (plugin UI iteration). Otherwise install and tell the user to restart.

How:
- Prefer these MCP tools over `killall calibre` / `open -a calibre`.
- install_plugin() default restart=false.
- If a tool returns error=locked, another agent is restarting. Skip. Do not kill Calibre.
- Pass a short agent_id so the lock holder is identifiable.
""".strip()


def _holder(tool: str, agent_id: str) -> str:
    name = (agent_id or os.environ.get("AO3KIT_DEV_AGENT") or "").strip()
    suffix = f":{name}" if name else ""
    return f"mcp:{tool}{suffix}"


def create_server(ctl: CalibreCtl | None = None) -> Any:
    from fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    app = FastMCP(name="calibre-dev", instructions=INSTRUCTIONS)
    calibre = ctl or CalibreCtl()

    @app.tool(
        name="calibre_status",
        annotations=ToolAnnotations(
            title="Calibre status",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def calibre_status() -> dict[str, Any]:
        """Is the Calibre GUI running, and is the restart lock held?"""
        return calibre.status()

    @app.tool(
        name="install_plugin",
        annotations=ToolAnnotations(
            title="Install Calibre plugin",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def install_plugin(
        restart: bool = False,
        agent_id: str = "",
        lock_timeout: float = 0.0,
    ) -> dict[str, Any]:
        """Zip and install the plugin into Calibre.

        restart defaults to false. Set restart=true only when this session
        needs the GUI to load the new zip now. That path quits Calibre.
        If the restart lock is busy, install still happened; do not kill Calibre.
        """
        return calibre.install(
            restart=restart,
            agent_id=agent_id,
            lock_timeout=lock_timeout,
            holder=_holder("install_plugin", agent_id),
        )

    @app.tool(
        name="restart_calibre",
        annotations=ToolAnnotations(
            title="Restart Calibre GUI",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    def restart_calibre(
        agent_id: str = "",
        lock_timeout: float = 0.0,
    ) -> dict[str, Any]:
        """Quit and start the Calibre GUI under the host-wide restart lock.

        Do not call after every install. Only when iterating on plugin UI now.
        If error=locked, another agent owns the restart; skip.
        """
        return calibre.restart(
            agent_id=agent_id,
            lock_timeout=lock_timeout,
            holder=_holder("restart_calibre", agent_id),
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="calibre_dev",
        description="FastMCP server for lock-aware Calibre plugin reloads.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Shared Streamable HTTP server instead of stdio (one coordinator).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="calibre_dev: %(message)s",
        stream=sys.stderr,
    )
    try:
        mcp = create_server()
    except ImportError:
        sys.stderr.write("fastmcp is required: pip install fastmcp\n")
        return 1
    if args.http:
        log.info("HTTP coordinator on %s:%s", args.host, args.port)
        mcp.run(transport="http", host=args.host, port=args.port)
        return 0
    mcp.run()
    return 0
