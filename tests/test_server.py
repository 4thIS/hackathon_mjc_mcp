"""로컬 서버(server.py)가 내보내는 도구 목록."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_local_server_exports_academic_calendar():
    code = (
        "import asyncio, server\n"
        "from mcp.client import Client\n"
        "async def names():\n"
        "    async with Client(server.mcp) as c:\n"
        "        return sorted(t.name for t in (await c.list_tools()).tools)\n"
        "print(asyncio.run(names()))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert "get_academic_calendar" in out.stdout
