"""학교 학사일정 조회.

학사일정 페이지는 서버가 HTML을 만들어 주는 정적 페이지로, 현재 학기 6개월치를
달력 형태로 보여 준다. 일정은 달마다 `div.memo` 안에 `h4`(기간)와 `p`(이름)
쌍으로 들어 있고, 여러 달에 걸친 일정은 걸친 달마다 되풀이된다.
"""

import re
from datetime import datetime

from mcp.types import ToolAnnotations

from common.cache import with_fallback
from common.errors import ParseError, ToolError
from common.http import fetch
from common.models import AcademicCalendar, CalendarEvent
from common.parse import parse_html

CALENDAR_URL = "https://www.mjc.ac.kr/collegeService/schedule.do"

_PERIOD = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\s*~\s*(\d{4})\.(\d{2})\.(\d{2})")


def parse_calendar(content: bytes) -> list[CalendarEvent]:
    soup = parse_html(content)

    events: list[CalendarEvent] = []
    seen: set[tuple[str, str, str]] = set()
    for memo in soup.select("div.memo"):
        for heading in memo.select("h4"):
            match = _PERIOD.search(heading.get_text(strip=True))
            name_element = heading.find_next_sibling("p")
            if match is None or name_element is None:
                raise ParseError("학사일정")
            y1, m1, d1, y2, m2, d2 = match.groups()
            event = CalendarEvent(
                name=name_element.get_text(strip=True),
                start=f"{y1}-{m1}-{d1}",
                end=f"{y2}-{m2}-{d2}",
            )
            # 여러 달에 걸친 일정은 달마다 되풀이되므로 한 번만 낸다.
            key = (event.name, event.start, event.end)
            if key not in seen:
                seen.add(key)
                events.append(event)

    if not events:
        raise ParseError("학사일정")
    return events


def _fetch_calendar() -> dict:
    events = parse_calendar(fetch(CALENDAR_URL, params={"menu_idx": "104"}))
    return {"events": [e.model_dump(mode="json") for e in events]}


def _check_date(name: str, value: str | None) -> None:
    if value is None:
        return
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ToolError(
            f"{name} 날짜 형식이 잘못되었습니다. YYYY-MM-DD 형식(예: 2026-10-01)으로 넘겨 주세요."
        ) from None


def get_academic_calendar(
    since: str | None = None, until: str | None = None
) -> AcademicCalendar:
    """명지전문대 학사일정(시험·수강신청·방학 등)을 조회한다.

    학교 페이지에 보이는 현재 학기 6개월치만 조회할 수 있다.
    since, until: 기간(YYYY-MM-DD, 당일 포함). 이 기간과 하루라도 겹치는 일정을
    모두 돌려준다. 예를 들어 10-20~10-26 중간고사는 since=10-25로 물어도 포함된다.
    """
    _check_date("since", since)
    _check_date("until", until)

    data, stale_age_min = with_fallback("academic_calendar", _fetch_calendar)
    events = [CalendarEvent.model_validate(e) for e in data["events"]]

    # 필터는 캐시에서 읽은 값에도 똑같이 적용되도록 조회 뒤에 한다.
    if since is not None:
        events = [e for e in events if e.end >= since]
    if until is not None:
        events = [e for e in events if e.start <= until]

    return AcademicCalendar(events=events, stale_age_min=stale_age_min)


def register(mcp) -> None:
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=True)
    mcp.tool(annotations=read_only)(get_academic_calendar)
