from pathlib import Path

import pytest

from common import cache
from common.errors import ParseError
from common.models import LibrarySeatReport, ReadingRoom
from tools import library_seats
from tools.library_seats import parse_seats

FIXTURE = Path(__file__).parent / "fixtures" / "library_seats.xml"


def test_parses_three_reading_rooms():
    rooms = parse_seats(FIXTURE.read_bytes())
    assert len(rooms) == 3
    assert [r.name for r in rooms] == ["집중학습공간", "개방형학습공간", "미디어실"]


def test_parses_seat_counts():
    rooms = {r.name: r for r in parse_seats(FIXTURE.read_bytes())}
    assert rooms["집중학습공간"].total == 72
    assert rooms["개방형학습공간"].total == 146
    assert rooms["미디어실"].total == 44


def test_available_and_in_use_are_integers():
    for room in parse_seats(FIXTURE.read_bytes()):
        assert isinstance(room.in_use, int)
        assert isinstance(room.available, int)
        assert room.in_use + room.available <= room.total


def test_raises_parse_error_when_no_items():
    with pytest.raises(ParseError):
        parse_seats(b"<root><data><page>1</page></data></root>")


def _room(total: int, in_use: int) -> ReadingRoom:
    return ReadingRoom(name="열람실", total=total, in_use=in_use, available=total - in_use)


def test_fixture_rooms_have_zero_usage():
    """기존 fixture는 세 열람실 모두 사용 0석이다."""
    for room in parse_seats(FIXTURE.read_bytes()):
        assert room.usage_rate == 0.0
        assert room.congestion == "여유"


@pytest.mark.parametrize(
    ("total", "in_use", "rate"),
    [(100, 49, 49.0), (72, 36, 50.0), (3, 1, 33.3), (146, 146, 100.0)],
)
def test_usage_rate_is_in_use_over_total_percent(total, in_use, rate):
    assert _room(total, in_use).usage_rate == rate


@pytest.mark.parametrize(
    ("total", "in_use", "level"),
    [
        (100, 0, "여유"),
        (100, 45, "여유"),
        (100, 46, "보통"),
        (100, 80, "보통"),
        (100, 81, "혼잡"),
        (100, 100, "혼잡"),
        # 반올림 전 값으로 나눈다: 표시가 45.0·80.0이어도 실제로 넘었으면 다음 단계다.
        (10001, 4501, "보통"),
        (2499, 2000, "혼잡"),
    ],
)
def test_congestion_level_boundaries(total, in_use, level):
    assert _room(total, in_use).congestion == level


def test_room_without_seats_has_no_rate_or_level():
    room = _room(0, 0)
    assert room.usage_rate is None
    assert room.congestion is None


def test_rate_and_level_are_in_the_dumped_response():
    dumped = _room(100, 90).model_dump(mode="json")
    assert dumped["usage_rate"] == 90.0
    assert dumped["congestion"] == "혼잡"


def test_report_explains_measured_at_and_operating_hours():
    report = LibrarySeatReport(rooms=[_room(10, 1)], measured_at="2026-10-28T15:00:00+09:00")
    assert "학교" in report.note and "갱신" in report.note
    assert "운영" in report.note


def test_old_cache_without_new_fields_still_loads():
    """새 필드가 생기기 전에 저장된 캐시도 읽혀야 하고, 이용률은 다시 계산된다."""
    report = LibrarySeatReport.model_validate(
        {
            "rooms": [{"name": "미디어실", "total": 44, "in_use": 11, "available": 33}],
            "measured_at": "2026-10-10T01:31:00+09:00",
            "stale_age_min": None,
        }
    )
    assert report.rooms[0].usage_rate == 25.0
    assert report.rooms[0].congestion == "여유"
    assert report.note


def test_cache_written_with_new_fields_loads_back():
    dumped = LibrarySeatReport(
        rooms=[_room(100, 90)], measured_at="2026-10-28T15:00:00+09:00"
    ).model_dump(mode="json")
    report = LibrarySeatReport.model_validate(dumped)
    assert report.rooms[0].congestion == "혼잡"


def test_get_library_seats_returns_rate_level_and_note(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(library_seats, "fetch", lambda *a, **kw: FIXTURE.read_bytes())
    dumped = library_seats.get_library_seats().model_dump(mode="json")
    assert all(r["usage_rate"] == 0.0 and r["congestion"] == "여유" for r in dumped["rooms"])
    assert dumped["note"]


def test_rate_and_level_are_described_in_the_tool_schema():
    """반환 모델의 스키마가 AI가 읽는 도구 설명이다. 새 값도 설명과 함께 나와야 한다."""
    props = LibrarySeatReport.model_json_schema()["$defs"]["ReadingRoom"]["properties"]
    assert props["usage_rate"]["description"]
    assert "45%" in props["congestion"]["description"]
    assert "80%" in props["congestion"]["description"]
