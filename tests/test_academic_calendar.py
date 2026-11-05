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


def test_rejects_reversed_range(calendar_env):
    with pytest.raises(ToolError, match="since가 until보다 늦습니다"):
        get_academic_calendar(since="2026-10-26", until="2026-10-20")


def test_validates_arguments_before_fetching(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(*a, **k):
        calls.append(k)
        return FIXTURE.read_bytes()

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(calendar_module, "fetch", fake_fetch)
    with pytest.raises(ToolError):
        get_academic_calendar(since="다음주")
    with pytest.raises(ToolError):
        get_academic_calendar(since="2026-10-26", until="2026-10-20")
    assert calls == []  # 학교로 요청이 나가지 않는다


def test_range_without_events_returns_empty_list(calendar_env):
    """해당 기간에 일정이 없는 것은 오류가 아니다."""
    assert get_academic_calendar(since="2030-01-01").events == []
    assert get_academic_calendar(until="2000-01-01").events == []


def test_raises_parse_error_when_event_name_is_missing():
    """정상 일정이 함께 있어도, 이름 없는 일정을 조용히 건너뛰지 않는다."""
    html = (
        "<div class='memo'>"
        "<h4>2026.10.01~2026.10.02</h4><p>정상 일정</p>"
        "<h4>2026.10.20~2026.10.26</h4>"
        "</div>"
    ).encode()
    with pytest.raises(ParseError):
        parse_calendar(html)


def test_event_without_name_does_not_borrow_the_next_name():
    """기간 바로 다음이 이름이 아니면, 뒤에 오는 다른 일정의 이름을 가져가지 않는다."""
    html = (
        "<div class='memo'>"
        "<h4>2026.10.20~2026.10.26</h4>"
        "<h4>2026.11.01~2026.11.02</h4><p>뒤 일정 이름</p>"
        "</div>"
    ).encode()
    with pytest.raises(ParseError):
        parse_calendar(html)


def test_period_allows_spaces_around_tilde():
    html = "<div class='memo'><h4>2026.10.20 ~ 2026.10.26</h4><p>시험</p></div>".encode()
    [event] = parse_calendar(html)
    assert (event.name, event.start, event.end) == ("시험", "2026-10-20", "2026-10-26")


def test_fetch_failure_without_cache_raises(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise FetchError("시험")

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(calendar_module, "fetch", fail)
    with pytest.raises(FetchError):
        get_academic_calendar()


def test_raises_parse_error_when_event_name_is_blank():
    """정상 일정이 함께 있어도, 이름이 빈 일정을 조용히 건너뛰지 않는다."""
    html = (
        "<div class='memo'>"
        "<h4>2026.09.01~2026.09.01</h4><p>개강일</p>"
        "<h4>2026.10.20~2026.10.26</h4><p> &nbsp; </p>"
        "</div>"
    ).encode()
    with pytest.raises(ParseError):
        parse_calendar(html)


def test_event_name_keeps_spaces_between_inline_tags():
    html = (
        "<div class='memo'><h4>2026.10.20~2026.10.26</h4>"
        "<p>2학기 <b>중간</b> 고사</p></div>"
    ).encode()
    [event] = parse_calendar(html)
    assert event.name == "2학기 중간 고사"


@pytest.mark.parametrize(
    "period",
    [
        "2026.13.45~2026.99.99",  # 없는 달·일
        "2026.02.30~2026.03.01",  # 없는 날짜
        "2026.10.26~2026.10.20",  # 시작이 종료보다 늦음
    ],
)
def test_raises_parse_error_when_period_is_not_a_real_range(period):
    """정상 일정이 함께 있어도, 기간이 틀린 일정을 조용히 건너뛰지 않는다."""
    html = (
        "<div class='memo'>"
        "<h4>2026.09.01~2026.09.01</h4><p>개강일</p>"
        f"<h4>{period}</h4><p>일정</p>"
        "</div>"
    ).encode()
    with pytest.raises(ParseError):
        parse_calendar(html)
