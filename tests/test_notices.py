import json
import time
from pathlib import Path

import pytest

from common import cache
from common.errors import FetchError, ParseError, ToolError
from common.models import NoticeDetail
from tools import notices as notices_module
from tools.notices import (
    get_notice,
    parse_notice_detail,
    parse_notice_list,
    search_notices,
)

FIXTURE = Path(__file__).parent / "fixtures" / "notices_academic.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "notice_detail.html"
DETAIL_URL = (
    "https://www.mjc.ac.kr/bbs/data/view.do"
    "?menu_idx=169&bbs_mst_idx=BM0000000270&data_idx=BD0000059046"
)


def test_parses_notices():
    notices = parse_notice_list(FIXTURE.read_bytes(), menu_idx=169)
    assert len(notices) > 0


def test_notice_has_title_and_korean_is_not_mojibake():
    notices = parse_notice_list(FIXTURE.read_bytes(), menu_idx=169)
    assert all(n.title for n in notices)
    joined = "".join(n.title for n in notices)
    assert "�" not in joined  # 인코딩이 깨지면 U+FFFD가 섞인다


def test_notice_id_encodes_menu_idx_and_codes():
    notices = parse_notice_list(FIXTURE.read_bytes(), menu_idx=169)
    menu_idx, bm, bd = notices[0].notice_id.split(":")
    assert menu_idx == "169"
    assert bm.startswith("BM")
    assert bd.startswith("BD")


def test_notice_date_and_views_are_parsed():
    notices = parse_notice_list(FIXTURE.read_bytes(), menu_idx=169)
    first = notices[0]
    assert len(first.posted_on) == 10 and first.posted_on[4] == "-"
    assert isinstance(first.views, int)
    assert first.department


def test_pinned_notices_are_flagged():
    notices = parse_notice_list(FIXTURE.read_bytes(), menu_idx=169)
    assert any(n.pinned for n in notices)


def test_raises_parse_error_when_table_missing():
    with pytest.raises(ParseError):
        parse_notice_list(b"<html><body>no table here</body></html>", menu_idx=169)


def test_parses_detail_metadata():
    detail = parse_notice_detail(DETAIL_FIXTURE.read_bytes(), DETAIL_URL)
    assert detail.title
    assert detail.department
    assert len(detail.posted_on) == 10
    assert isinstance(detail.views, int)


def test_detail_body_is_never_empty():
    """본문이 이미지로만 작성된 공지가 흔하다. 빈 문자열을 돌려주면 안 된다."""
    detail = parse_notice_detail(DETAIL_FIXTURE.read_bytes(), DETAIL_URL)
    assert detail.body.strip()


def test_detail_has_no_mojibake():
    detail = parse_notice_detail(DETAIL_FIXTURE.read_bytes(), DETAIL_URL)
    assert "�" not in detail.title + detail.body


def test_detail_raises_parse_error_when_view_missing():
    with pytest.raises(ParseError):
        parse_notice_detail(b"<html><body>nothing</body></html>", DETAIL_URL)


def test_get_notice_rejects_malformed_id():
    with pytest.raises(ParseError):
        get_notice("not-a-valid-id")


def test_detail_collects_body_image_urls():
    """본문이 이미지뿐인 공지에서는 이미지 링크가 유일한 내용이다."""
    detail = parse_notice_detail(DETAIL_FIXTURE.read_bytes(), DETAIL_URL)
    assert detail.body_images
    assert all(url.startswith("https://") for url in detail.body_images)


def test_detail_body_notice_points_to_image_links():
    detail = parse_notice_detail(DETAIL_FIXTURE.read_bytes(), DETAIL_URL)
    assert "body_images" in detail.body
    assert "source_url" in detail.body


def test_detail_source_url_is_the_given_page_url():
    detail = parse_notice_detail(DETAIL_FIXTURE.read_bytes(), DETAIL_URL)
    assert detail.source_url == DETAIL_URL
    assert detail.source_url.startswith("https://www.mjc.ac.kr/bbs/data/view.do?")


def test_relative_image_src_becomes_absolute_url():
    html = (
        b"<div class='board_view'><div id='divMemo'>"
        b"<img src='/upload/a.png'><img src='b.png'><img src=''><img>"
        b"</div></div>"
    )
    detail = parse_notice_detail(html, DETAIL_URL)
    assert detail.body_images == [
        "https://www.mjc.ac.kr/upload/a.png",
        "https://www.mjc.ac.kr/bbs/data/b.png",
    ]


def test_detail_without_images_has_empty_body_images():
    html = b"<div class='board_view'><div id='divMemo'>\xea\xb8\x80\xec\x9e\x90</div></div>"
    detail = parse_notice_detail(html, DETAIL_URL)
    assert detail.body_images == []
    assert detail.body == "글자"


def test_detail_attachments_and_truncated_still_work():
    detail = parse_notice_detail(DETAIL_FIXTURE.read_bytes(), DETAIL_URL)
    assert detail.truncated is False
    assert all(detail.attachments)

    long_body = "가" * 5000
    html = (
        "<div class='board_view'><div id='divMemo'>"
        f"{long_body}</div>"
        "<a href=\"javascript:fn_egov_downFile('X','1')\">첨부.hwp</a>"
        "</div>"
    ).encode("utf-8")
    detail = parse_notice_detail(html, DETAIL_URL)
    assert detail.truncated is True
    assert len(detail.body) == 4000
    assert detail.attachments == ["첨부.hwp"]


def test_cached_detail_without_new_fields_still_loads():
    """기존 캐시에는 새 필드가 없다. 기본값으로 복원되어야 한다."""
    detail = NoticeDetail.model_validate(
        {
            "title": "제목",
            "department": "학사지원처",
            "posted_on": "2026-08-04",
            "views": 1,
            "body": "본문",
            "attachments": [],
            "truncated": False,
        }
    )
    assert detail.source_url == ""
    assert detail.body_images == []


# --- ms-01: 첨부 표시·날짜 필터·정렬·고정 글 제외 ---

@pytest.fixture
def list_env(tmp_path, monkeypatch):
    """학교 서버 대신 학사 목록 fixture를 돌려주고 캐시는 임시 폴더에 둔다."""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(notices_module, "fetch", lambda *a, **k: FIXTURE.read_bytes())
    return tmp_path


def test_list_marks_attachment_presence():
    notices = parse_notice_list(FIXTURE.read_bytes(), menu_idx=169)
    assert notices[0].has_attachment is True  # 첫 고정 글은 첨부 아이콘이 있다
    assert notices[1].has_attachment is False  # 두 번째 고정 글은 없다


def test_search_default_keeps_board_order_with_pinned_first(list_env):
    result = search_notices(limit=100)
    assert result.notices == parse_notice_list(FIXTURE.read_bytes(), menu_idx=169)
    assert result.notices[0].pinned


def test_search_filters_by_posted_date_inclusive(list_env):
    result = search_notices(since="2026-07-20", until="2026-08-04", limit=100)
    dates = [n.posted_on for n in result.notices]
    assert dates
    assert all("2026-07-20" <= d <= "2026-08-04" for d in dates)
    assert "2026-07-20" in dates  # 시작일 당일 포함
    assert "2026-08-04" in dates  # 종료일 당일 포함
    assert "2026-08-06" not in dates


@pytest.mark.parametrize("bad", ["2026/07/20", "2026-7-20", "20260720", "2026-13-01", "어제"])
def test_search_rejects_malformed_date(list_env, bad):
    with pytest.raises(ToolError):
        search_notices(since=bad)
    with pytest.raises(ToolError):
        search_notices(until=bad)


def test_search_sorts_newest_first_ignoring_pinned(list_env):
    result = search_notices(sort="newest", limit=100)
    dates = [n.posted_on for n in result.notices]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == "2026-08-06"  # 고정 글보다 새로운 일반 글이 맨 앞


def test_search_sorts_oldest_first(list_env):
    result = search_notices(sort="oldest", limit=100)
    dates = [n.posted_on for n in result.notices]
    assert dates == sorted(dates)


def test_search_can_exclude_pinned(list_env):
    result = search_notices(exclude_pinned=True, limit=100)
    assert result.notices
    assert not any(n.pinned for n in result.notices)


def test_search_applies_limit_after_filter_and_sort(list_env):
    result = search_notices(sort="newest", exclude_pinned=True, limit=2)
    assert [n.posted_on for n in result.notices] == ["2026-08-06", "2026-07-20"]


def test_search_filters_cached_list_when_fetch_fails(list_env, monkeypatch):
    """필터·정렬은 캐시에서 읽은 뒤에도 똑같이 적용된다."""
    search_notices()  # 캐시 저장

    def fail(*a, **k):
        raise FetchError("시험")

    monkeypatch.setattr(notices_module, "fetch", fail)
    result = search_notices(exclude_pinned=True, since="2026-07-01", limit=100)
    assert result.stale_age_min is not None
    assert result.notices
    assert all(not n.pinned and n.posted_on >= "2026-07-01" for n in result.notices)


def test_old_cache_without_attachment_field_still_loads(list_env, monkeypatch):
    old = {
        "notices": [
            {
                "notice_id": "169:BM1:BD1",
                "title": "옛 캐시 글",
                "department": "학사지원팀",
                "posted_on": "2026-08-01",
                "views": 3,
                "pinned": False,
            }
        ]
    }
    (list_env / "notices_academic.json").write_text(
        json.dumps({"saved_at": time.time(), "data": old}, ensure_ascii=False),
        encoding="utf-8",
    )

    def fail(*a, **k):
        raise FetchError("시험")

    monkeypatch.setattr(notices_module, "fetch", fail)
    result = search_notices(sort="newest")
    assert result.notices[0].title == "옛 캐시 글"
    assert result.notices[0].has_attachment is None  # 모름
