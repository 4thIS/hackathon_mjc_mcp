"""명지전문대 MCP 서버 진입점.

stdio transport에서 stdout은 JSON-RPC 채널이다. 어떤 모듈에서도
print()를 쓰면 안 되며, 로그는 반드시 stderr로 나가야 한다.
"""

import logging
import sys

logging.basicConfig(stream=sys.stderr, level=logging.INFO)

# 표준입출력 인코딩을 명시적으로 UTF-8로 고정한다. mcp.run()이 stdio 통로를
# 넘겨받기 전, import 시점의 좁은 창에서만 실질적 의미가 있지만(그 이후엔 SDK가
# 별도 통로를 쓴다), 로케일이 예상과 다른 환경(Windows cp949 콘솔, 리눅스 C
# 로케일 등)에서 한글 처리 시 UnicodeEncodeError/UnicodeDecodeError가 나는 것을
# 막는 방어선으로 둔다. 특정 OS 전용 문제가 아니므로 stdin/stdout 둘 다 고정한다.
for _stream in (sys.stdin, sys.stdout):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from mcp.server import MCPServer  # noqa: E402

from tools import academic_calendar, course_search, departments, library_seats, notices, syllabus  # noqa: E402

mcp = MCPServer("mjc")
library_seats.register(mcp)
notices.register(mcp)
departments.register(mcp)
academic_calendar.register(mcp)
course_search.register(mcp)
syllabus.register(mcp)


if __name__ == "__main__":
    mcp.run()
