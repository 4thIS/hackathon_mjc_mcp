"""조회 실패 시 마지막 성공 데이터로 폴백한다.

라이브 데모 중 학교 서버나 시연장 네트워크가 죽어도 툴이 답을 내놓게 하는 안전망.
캐시된 값을 반환할 때는 몇 분 전 것인지 함께 알려 AI가 실시간 값으로 오인하지 않게 한다.

안전망이 본체를 죽이지 않는다: 캐시 쓰기가 실패해도 이미 받은 데이터를 그대로 돌려주고,
캐시 읽기가 실패하면 원래의 ToolError를 올린다.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable

from common.errors import ToolError


def _default_cache_dir() -> Path:
    """사용자 캐시 폴더 아래 mjc-mcp/cache. 로그인 세션 폴더(common/session.py)와 같은 사용자 캐시 폴더를 쓴다.

    코드 폴더를 기준으로 하면 패키지로 설치했을 때 site-packages 안이 되어 쓸 수 없거나 여러 프로젝트가 함께 쓴다.
    폴더는 캐시를 처음 쓸 때 만든다.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache"))
    return base / "mjc-mcp" / "cache"


# 캐시 위치. 환경 변수 MJC_CACHE_DIR로 바꿀 수 있고, 없거나 비어 있으면 사용자 캐시 폴더다.
CACHE_DIR = Path(os.environ.get("MJC_CACHE_DIR") or _default_cache_dir()).expanduser()

# stdout은 MCP stdio transport의 JSON-RPC 채널이므로 로그는 stderr로만 내보낸다.
_logger = logging.getLogger(__name__)
if not _logger.handlers:
    _logger.addHandler(logging.StreamHandler(sys.stderr))
_logger.propagate = False


def _read_cache(path: Path) -> tuple[dict, int] | None:
    """캐시를 읽어 (데이터, 나이(분))을 반환한다. 없거나 깨졌으면 None."""
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        age_min = int((time.time() - cached["saved_at"]) / 60)
        return cached["data"], age_min
    except (OSError, ValueError, KeyError, TypeError):
        # 파일명은 호출자가 정한 고정 key라 원문 유출 위험이 없다.
        _logger.warning("캐시를 읽지 못했습니다: %s", path.name)
        return None


def _write_cache(path: Path, data: dict) -> None:
    """캐시 저장은 실패해도 무시한다. 이미 확보한 응답을 죽이지 않기 위해서다."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"saved_at": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
    except (OSError, ValueError, TypeError):
        _logger.warning("캐시를 저장하지 못했습니다: %s", path.name)


def with_fallback(
    key: str, fetch_fn: Callable[[], dict]
) -> tuple[dict, int | None]:
    """(데이터, 캐시 나이(분)) 를 반환한다. 실시간 조회에 성공하면 나이는 None.

    캐시도 없이 조회에 실패하면 원래 에러를 그대로 올린다.
    """
    path = CACHE_DIR / f"{key}.json"

    try:
        data = fetch_fn()
    except ToolError:
        cached = _read_cache(path)
        if cached is None:
            raise
        return cached

    _write_cache(path, data)
    return data, None
