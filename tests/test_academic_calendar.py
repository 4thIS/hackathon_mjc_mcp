import json
import time
from pathlib import Path

import pytest

from common import cache
from common.errors import FetchError, ParseError, ToolError
from tools import academic_calendar as calendar_module
from tools.academic_calendar import get_academic_calendar, parse_calendar

FIXTURE = Path(__file__).parent / "fixtures" / "academic_calendar.html"


@pytest.fixture
def calendar_env(tmp_path, monkeypatch):
    """학교 서버 대신 학사일정 fixture를 돌려주고 캐시는 임시 폴더에 둔다."""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(calendar_module, "fetch", lambda *a, **k: FIXTURE.read_bytes())
    return tmp_path


def test_parses_events_without_duplicates():
    """같은 일정이 걸친 달마다 반복되지만 한 번만 낸다(2026-10-18 실측 26건)."""
    events = parse_calendar(FIXTURE.read_bytes())
    assert len(events) == 26
    keys = [(e.name, e.start, e.end) for e in events]
    assert len(keys) == len(set(keys))


def test_event_dates_are_iso_and_names_are_korean():
    events = parse_calendar(FIXTURE.read_bytes())
    midterm = next(e for e in events if e.name == "2학기 중간고사")
    assert (midterm.start, midterm.end) == ("2026-10-20", "2026-10-26")
    assert "�" not in "".join(e.name for e in events)
    assert all(e.start <= e.end for e in events)


def test_same_day_different_events_are_both_kept():
    events = parse_calendar(FIXTURE.read_bytes())
    names = {e.name for e in events if e.start == "2026-09-01"}
    assert names == {"2026학년도 2학기 개시일", "2026학년도 2학기 개강일"}


def test_raises_parse_error_when_no_events():
    with pytest.raises(ParseError):
        parse_calendar(b"<html><body><div class='calendar_group'></div></body></html>")


def test_raises_parse_error_when_date_is_malformed():
    html = "<div class='memo'><h4>곧 공지 예정</h4><p>무언가</p></div>".encode()
    with pytest.raises(ParseError):
        parse_calendar(html)


def test_tool_returns_all_events_without_filter(calendar_env):
    result = get_academic_calendar()
    assert len(result.events) == 26
    assert result.stale_age_min is None


def test_filter_includes_events_that_partly_overlap(calendar_env):
    """2026-10-25~10-31을 물으면 10-20~10-26 중간고사도 걸쳐 있으므로 포함한다."""
    result = get_academic_calendar(since="2026-10-25", until="2026-10-31")
    assert [e.name for e in result.events] == ["2학기 중간고사"]


def test_filter_bounds_are_inclusive(calendar_env):
    result = get_academic_calendar(since="2026-10-26", until="2026-10-26")
    assert [e.name for e in result.events] == ["2학기 중간고사"]
    result = get_academic_calendar(since="2026-10-20", until="2026-10-20")
    assert [e.name for e in result.events] == ["2학기 중간고사"]


def test_filter_with_only_since_or_until(calendar_env):
    after = get_academic_calendar(since="2027-02-19")
    assert [e.name for e in after.events] == ["제52회 전기 학위수여식", "2027학년도 입학식"]
    before = get_academic_calendar(until="2026-09-01")
    assert {e.name for e in before.events} == {"2026학년도 2학기 개시일", "2026학년도 2학기 개강일"}


@pytest.mark.parametrize("bad", ["2026/10/20", "2026-1-20", "20261020", "2026-02-30", "다음주"])
def test_rejects_malformed_date(calendar_env, bad):
    with pytest.raises(ToolError):
        get_academic_calendar(since=bad)
    with pytest.raises(ToolError):
        get_academic_calendar(until=bad)


def test_uses_cache_when_fetch_fails(calendar_env, monkeypatch):
    get_academic_calendar()

    def fail(*a, **k):
        raise FetchError("시험")

    monkeypatch.setattr(calendar_module, "fetch", fail)
    result = get_academic_calendar(since="2026-10-25", until="2026-10-31")
    assert result.stale_age_min is not None
    assert [e.name for e in result.events] == ["2학기 중간고사"]
