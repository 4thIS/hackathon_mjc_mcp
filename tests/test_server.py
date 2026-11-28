"""로컬 서버(server.py)가 내보내는 도구 목록."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# server.py는 패키지에 넣지 않으므로, 설치된 패키지로 테스트를 돌릴 때(CI의 두 번째 단계)는 없다.
pytestmark = pytest.mark.skipif(not (ROOT / "server.py").exists(), reason="리포 체크아웃에서만 있는 로컬 서버")


def test_local_server_exports_academic_calendar():
    code = (
        "import asyncio, server\n"
        "from mcp.client import Client\n"
        "async def names():\n"
        "    async with Client(server.mcp) as c:\n"
        "        return sorted(t.name for t in (await c.list_tools()).tools)\n"
        "print(asyncio.run(names()))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "get_academic_calendar" in out.stdout
