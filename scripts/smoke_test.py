"""Start the server over stdio (like an MCP client would), list the tools and
call the ones that don't change anything.

    uv run python scripts/smoke_test.py            # status + list tools
    uv run python scripts/smoke_test.py --live     # also a screenshot (focuses VRoid)
    uv run python scripts/smoke_test.py --vrm file.vrm
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def show(result) -> None:
    for c in result.content:
        if c.type == "text":
            print(c.text[:1500])
        elif c.type == "image":
            print(f"[image {c.mime_type}, {len(base64.b64decode(c.data)) // 1024} KB]")
    if result.is_error:
        print("(is_error)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--vrm")
    args = ap.parse_args()
    params = StdioServerParameters(command="uv", args=["run", "--directory", str(ROOT), "vroid-mcp"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        tools = await session.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools))
        print("\n== vroid_status"); show(await session.call_tool("vroid_status", {}))
        if args.vrm:
            print("\n== vroid_inspect_vrm")
            show(await session.call_tool("vroid_inspect_vrm", {"path": args.vrm}))
        print("\n== vroid_inspect_vrm (missing file)")
        show(await session.call_tool("vroid_inspect_vrm", {"path": "nope.vrm"}))
        if args.live:
            print("\n== vroid_screenshot")
            show(await session.call_tool("vroid_screenshot", {"max_px": 800}))
            print("\n== vroid_find_text Body")
            show(await session.call_tool("vroid_find_text", {"query": "Body"}))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
