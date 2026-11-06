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


def test_search_rejects_reversed_date_range(list_env):
    with pytest.raises(ToolError, match="since가 until보다 늦습니다"):
        search_notices(since="2026-08-04", until="2026-07-20")


def test_search_accepts_single_day_range(list_env):
    result = search_notices(since="2026-08-03", until="2026-08-03", limit=100)
    assert [n.posted_on for n in result.notices] == ["2026-08-03"] * 3


def test_search_validates_arguments_before_fetching(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(*a, **k):
        calls.append(k)
        return FIXTURE.read_bytes()

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(notices_module, "fetch", fake_fetch)
    with pytest.raises(ToolError):
        search_notices(since="어제")
    with pytest.raises(ToolError):
        search_notices(since="2026-08-04", until="2026-07-20")
    assert calls == []  # 학교로 요청이 나가지 않는다


@pytest.mark.parametrize("sort", ["newest", "oldest"])
def test_sort_keeps_board_order_among_same_day_notices(list_env, sort):
    def same_day(result):
        return [n.title for n in result.notices if n.posted_on == "2026-08-03"]

    board = same_day(search_notices(limit=100))
    assert len(board) == 3
    assert same_day(search_notices(sort=sort, limit=100)) == board


def test_row_without_attachment_column_is_not_marked():
    html = (
        "<table class='board_list'><tr>"
        "<td class='cell_type01'><a href=\"javascript:fn_view('BM1','BD1','');\">제목</a></td>"
        "<td>2026-08-01</td><td>3</td>"
        "</tr></table>"
    ).encode("utf-8")
    [notice] = parse_notice_list(html, menu_idx=169)
    assert notice.has_attachment is False


def test_search_sorts_before_applying_limit(list_env):
    """자르기가 정렬보다 먼저면 게시판 앞 2건(08-04, 08-03)이 나온다."""
    result = search_notices(sort="oldest", limit=2)
    assert [n.posted_on for n in result.notices] == ["2022-01-13", "2026-05-28"]


def test_search_without_matching_notices_returns_empty_list(list_env):
    """조건에 맞는 글이 없는 것은 오류가 아니다."""
    assert search_notices(since="2030-01-01").notices == []


# --- ms-02: 제목 키워드·다음 쪽·limit 상한 ---

P2_FIXTURE = Path(__file__).parent / "fixtures" / "notices_academic_p2.html"

# 마지막 쪽 너머를 요청하면 학교는 빈 표가 아니라 상단 고정 글만 돌려준다
# (학사공지 pageIndex=9999, 2026-11-06 실측: 고정 5건, 일반 0건).
# 그 구조만 남긴 최소 재현본이다.
_PINNED_ONLY_PAGE = (
    "<table class='board_list'><tr class='cell_notice'>"
    "<td><img alt='공지'></td>"
    "<td class='cell_type01'><a href=\"javascript:fn_view('BM1','BD1','');\">고정 글</a></td>"
    "<td></td><td>학사지원팀</td><td>2026-10-26</td><td>7</td>"
    "</tr></table>"
).encode("utf-8")


@pytest.fixture
def paged_env(tmp_path, monkeypatch):
    """쪽 번호에 맞는 응답을 돌려주고, 보낸 요청 인자를 기록한다."""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    calls: list[dict] = []

    def fake_fetch(url, *, params=None, headers=None):
        calls.append(dict(params or {}))
        page = (params or {}).get("pageIndex")
        if page == "999":
            return _PINNED_ONLY_PAGE
        return (P2_FIXTURE if page == "2" else FIXTURE).read_bytes()

    monkeypatch.setattr(notices_module, "fetch", fake_fetch)
    return calls


def test_first_page_request_is_unchanged(paged_env):
    search_notices()
    assert paged_env == [{"menu_idx": "169"}]


def test_next_page_sends_page_index(paged_env):
    search_notices(page=2)
    assert paged_env == [{"menu_idx": "169", "pageIndex": "2"}]


def test_second_page_fixture_repeats_pinned_notices():
    """전제 고정: 학교는 2쪽에도 고정 글을 다시 내려 준다."""
    assert any(n.pinned for n in parse_notice_list(P2_FIXTURE.read_bytes(), 169))


def test_next_page_drops_repeated_pinned_notices(paged_env):
    result = search_notices(page=2, limit=50)
    assert result.notices
    assert not any(n.pinned for n in result.notices)
    assert [n.posted_on for n in result.notices][:2] == ["2026-08-12", "2026-08-10"]


@pytest.mark.parametrize("bad", [0, -1])
def test_page_must_be_positive(paged_env, bad):
    with pytest.raises(ToolError, match="page"):
        search_notices(page=bad)
    assert paged_env == []  # 학교로 요청이 나가지 않는다


def test_page_beyond_last_returns_empty_list(paged_env):
    """마지막 쪽 너머는 고정 글만 오고, 그 고정 글은 2쪽부터 빠지므로 빈 목록이 된다."""
    assert search_notices(page=999).notices == []


def test_keyword_filters_by_title(paged_env):
    titles = [n.title for n in search_notices(keyword="등록금", limit=50).notices]
    assert titles == [
        "2026학년도 2학기 재학생 등록금 분할납부 관련 안내",
        "2026학년도 2학기 등록금 납부일정 안내",
    ]


def test_keyword_ignores_case_and_surrounding_spaces(paged_env):
    titles = [n.title for n in search_notices(keyword="  k-mooc ", limit=50).notices]
    assert titles == ["2026학년도 제1학기 K-MOOC 학점인정 신청 안내"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_keyword_means_no_filter(paged_env, blank):
    assert search_notices(keyword=blank, limit=50).notices == search_notices(limit=50).notices


def test_keyword_applies_before_limit(paged_env):
    result = search_notices(keyword="등록금", limit=1)
    assert [n.title for n in result.notices] == [
        "2026학년도 2학기 재학생 등록금 분할납부 관련 안내"
    ]


def test_keyword_filters_within_the_requested_page_only(paged_env):
    """같은 키워드라도 조회한 쪽 안에서만 거른다."""
    assert len(search_notices(keyword="등록금", limit=50).notices) == 2
    assert len(search_notices(page=2, keyword="등록금", limit=50).notices) == 2


def test_limit_is_capped(paged_env, monkeypatch):
    assert notices_module.MAX_LIMIT == 50
    monkeypatch.setattr(notices_module, "MAX_LIMIT", 3)
    assert len(search_notices(limit=10_000).notices) == 3


def test_next_page_does_not_fall_back_to_first_page_cache(paged_env, monkeypatch):
    """2쪽 조회가 실패했을 때 1쪽 캐시를 2쪽인 것처럼 돌려주면 안 된다."""
    search_notices()  # 1쪽만 캐시됨

    def fail(*a, **k):
        raise FetchError("시험")

    monkeypatch.setattr(notices_module, "fetch", fail)
    with pytest.raises(FetchError):
        search_notices(page=2)
    assert search_notices().stale_age_min is not None  # 1쪽은 자기 캐시로 답한다


def test_next_page_falls_back_to_its_own_cache(paged_env, monkeypatch):
    fresh = search_notices(page=2, limit=50)

    def fail(*a, **k):
        raise FetchError("시험")

    monkeypatch.setattr(notices_module, "fetch", fail)
    stale = search_notices(page=2, limit=50)
    assert stale.stale_age_min is not None
    assert stale.notices == fresh.notices


def test_old_first_page_cache_is_still_read(paged_env, monkeypatch):
    """1쪽 캐시 키는 그대로다. ms-02 이전에 저장된 캐시가 그대로 읽힌다."""
    search_notices()
    assert (paged_env and (Path(str(cache.CACHE_DIR)) / "notices_academic.json").exists())
