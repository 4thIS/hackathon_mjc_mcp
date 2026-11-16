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


# --- ms-04: 본문 평탄화 ---

TEXT_DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "notice_detail_text.html"
TEXT_DETAIL_URL = (
    "https://www.mjc.ac.kr/bbs/data/view.do"
    "?menu_idx=169&bbs_mst_idx=BM0000000025&data_idx=BD0050388098"
)


def _body(inner: str) -> str:
    html = f"<div class='board_view'><div id='divMemo'>{inner}</div></div>"
    return parse_notice_detail(html.encode("utf-8"), DETAIL_URL).body


def test_inline_tags_are_joined_without_newlines():
    assert _body("<p><span>2026</span><span>학년도 </span><b>2</b>학기</p>") == "2026학년도 2학기"


def test_block_boundaries_become_newlines():
    assert _body("<p>첫 줄</p><div>둘째 줄</div>셋째<br>넷째") == "첫 줄\n둘째 줄\n셋째\n넷째"


def test_source_whitespace_collapses_and_empty_blocks_vanish():
    assert _body("<p>  가\n   나  </p><p><br></p><p>&nbsp;</p><p>다</p>") == "가 나\n다"


def test_comments_and_scripts_are_not_body_text():
    assert _body("<!-- 숨김 --><script>var a=1;</script><style>p{}</style><p>본문</p>") == "본문"


def test_table_rows_become_one_line_each_in_order():
    body = _body(
        "<p>안내</p><table>"
        "<tr><th>구분</th><th>기간</th></tr>"
        "<tr><td><p>1차</p></td><td><p>09.01.~</p><p>09.30.</p></td></tr>"
        "<tr><td>2차</td><td><span>10.</span><span>01.</span></td></tr>"
        "</table><p>끝</p>"
    )
    assert body == "안내\n구분 | 기간\n1차 | 09.01.~ 09.30.\n2차 | 10.01.\n끝"


def test_real_text_notice_is_not_fragmented():
    body = parse_notice_detail(TEXT_DETAIL_FIXTURE.read_bytes(), TEXT_DETAIL_URL).body
    assert "학사내규 제2조(등록)" in body
    assert "1. 학기 개시일 : 2026.09.01" in body
    assert "학기 개시일로부터 30일까지 | 2026.09.01.~ 2026.09.30. | 등록금의 6분의 5 해당액" in body
    assert "\n(\n" not in body  # 괄호가 따로 한 줄을 차지하던 문제
    assert "개발 확인 사항" not in body  # HTML 주석


def test_real_text_notice_keeps_image_links_and_is_not_image_only():
    detail = parse_notice_detail(TEXT_DETAIL_FIXTURE.read_bytes(), TEXT_DETAIL_URL)
    assert "body_images" not in detail.body
    assert len(detail.body_images) == 2
    assert detail.truncated is False


def test_inline_tags_nested_200_levels_deep_are_read():
    assert _body("<span>" * 200 + "글자" + "</span>" * 200) == "글자"


def test_list_items_become_one_line_each():
    assert _body("<ul><li>하나</li><li><b>둘</b>째</li></ul><ol><li>셋</li></ol>") == "하나\n둘째\n셋"


def test_empty_cell_keeps_its_column_position():
    assert _body("<table><tr><td>가</td><td></td><td>다</td></tr></table>") == "가 |  | 다"


def test_row_with_only_empty_cells_is_dropped():
    assert _body("<table><tr><td>&nbsp;</td><td></td></tr><tr><td>값</td></tr></table>") == "값"


def test_table_with_tbody_and_nested_table_keeps_order():
    body = _body(
        "<table><tbody><tr>"
        "<td>밖<table><tr><td>안1</td><td>안2</td></tr></table></td>"
        "<td>옆</td>"
        "</tr></tbody></table>"
    )
    assert body == "밖 안1 | 안2 | 옆"


def test_too_deeply_nested_body_raises_parse_error():
    """파이썬 재귀 한도를 넘게 겹친 본문은 RecursionError가 아니라 ParseError여야 한다.

    ParseError는 ToolError라서 get_notice의 캐시 폴백이 동작한다.
    """
    with pytest.raises(ParseError):
        _body("<span>" * 2000 + "글자" + "</span>" * 2000)


def test_single_cell_row_keeps_its_lines():
    """칸이 하나뿐인 행은 테두리 상자다. 칸 안의 문단·목록을 한 줄로 뭉치지 않는다."""
    body = _body(
        "<table><tr><td>"
        "<p>1. 신청기간: 9.1.~9.5.</p><p>2. 신청방법: 온라인</p>"
        "<ul><li>유의 하나</li><li>유의 둘</li></ul>"
        "</td></tr></table>"
    )
    assert body == "1. 신청기간: 9.1.~9.5.\n2. 신청방법: 온라인\n유의 하나\n유의 둘"


@pytest.mark.parametrize(
    "tag",
    ["center", "main", "aside", "nav", "figcaption", "details", "summary", "fieldset", "form"],
)
def test_other_block_tags_also_break_lines(tag):
    assert _body(f"<{tag}>제목</{tag}><{tag}>부제</{tag}>") == "제목\n부제"


# --- ms-04: 병합 칸(rowspan·colspan) 풀기 ---

MERGED_FIXTURE = Path(__file__).parent / "fixtures" / "notice_detail_merged_cells.html"
MERGED_URL = (
    "https://www.mjc.ac.kr/bbs/data/view.do"
    "?menu_idx=208&bbs_mst_idx=BM0000000032&data_idx=BD0000001001"
)


def _merged_table() -> list[list[str]]:
    """장학종별 표의 각 줄을 칸 목록으로 돌려준다."""
    body = parse_notice_detail(MERGED_FIXTURE.read_bytes(), MERGED_URL).body
    return [line.split(" | ") for line in body.split("\n") if " | " in line]


def _merged_row(first_cell: str) -> list[str]:
    [row] = [cells for cells in _merged_table() if cells[0] == first_cell]
    return row


def test_real_merged_cells_fill_rows_below():
    """대상·신청기간이 위 행에서 rowspan으로 걸쳐 있어도 어학우수 장학 줄에 들어간다."""
    row = _merged_row("어학우수 장학")
    assert row[3] == "재학생 (본과정) ※ 복학생 포함"
    assert row[4].startswith("1차 신청: 9/1~9/30")
    assert row[5:] == ["학과장", "정액 30만원"]


def test_real_merged_cells_keep_column_positions():
    """명지우수장학 줄의 네 번째 칸은 학과장이 아니라 대상이다."""
    row = _merged_row("명지우수장학")
    assert row[3] == "재학생 (본과정) ※ 복학생 포함"
    assert row[4].startswith("학기 중 접수")
    assert row[5] == "학과장"


def test_real_merged_table_rows_all_have_header_width():
    table = _merged_table()
    assert table[0][:4] == ["장학종별", "신청기준", "제출서류", "대상"]
    assert len(table) == 10
    assert all(len(cells) == 7 for cells in table)
    # 교직원장학 이름 칸은 세 행에 걸쳐 있다. 세 행 모두 이름과 대상이 채워진다.
    staff = [cells for cells in table if cells[0].startswith("교직원장학")]
    assert [cells[6] for cells in staff] == ["등록금의 100% 범위내", "수업료의 50% 범위내", "등록금의 50% 범위내"]
    assert len({cells[3] for cells in staff}) == 1


def test_rowspan_cell_repeats_in_rows_below():
    body = _body(
        "<table>"
        "<tr><th>구분</th><th>기간</th><th>금액</th></tr>"
        "<tr><td rowspan='2'>1학기</td><td>3월</td><td>100</td></tr>"
        "<tr><td>4월</td><td>200</td></tr>"
        "</table>"
    )
    assert body == "구분 | 기간 | 금액\n1학기 | 3월 | 100\n1학기 | 4월 | 200"


def test_colspan_cell_repeats_across_columns():
    body = _body(
        "<table>"
        "<tr><th colspan='2'>신청</th><th>비고</th></tr>"
        "<tr><td>시작</td><td>끝</td><td>없음</td></tr>"
        "</table>"
    )
    assert body == "신청 | 신청 | 비고\n시작 | 끝 | 없음"


def test_row_left_with_one_cell_by_rowspan_stays_one_line():
    """rowspan 때문에 칸이 하나만 남은 행은 상자가 아니라 표의 한 행이다."""
    body = _body(
        "<table>"
        "<tr><td rowspan='2'>1학년</td><td>기간: 9.1.<br>장소: 본관</td></tr>"
        "<tr><td>기간: 9.2.<br>장소: 별관</td></tr>"
        "</table>"
    )
    assert body == "1학년 | 기간: 9.1. 장소: 본관\n1학년 | 기간: 9.2. 장소: 별관"


@pytest.mark.parametrize("value", ["abc", "0", "-3", ""])
def test_invalid_span_values_count_as_one(value):
    body = _body(
        "<table>"
        f"<tr><td rowspan='{value}' colspan='{value}'>가</td><td>나</td></tr>"
        "<tr><td>다</td><td>라</td></tr>"
        "</table>"
    )
    assert body == "가 | 나\n다 | 라"


def test_rowspan_does_not_leak_into_next_table():
    body = _body(
        "<table><tr><td rowspan='5'>위</td><td>값</td></tr></table>"
        "<table><tr><td>아래</td><td>표</td></tr></table>"
    )
    assert body == "위 | 값\n아래 | 표"


def test_colspan_row_consumes_overlapped_rowspan():
    """전폭 행이 세로 병합된 열을 덮으면, 그 값이 아래 행에 끼어들지 않는다."""
    body = _body(
        "<table>"
        "<tr><td>A1</td><td rowspan='2'>B</td><td>C1</td></tr>"
        "<tr><td colspan='3'>전폭 안내</td></tr>"
        "<tr><td>A3</td><td>B3</td><td>C3</td></tr>"
        "</table>"
    )
    assert body == "A1 | B | C1\n전폭 안내\nA3 | B3 | C3"


@pytest.mark.parametrize("span", ["colspan='3'", "rowspan='2' colspan='2'"])
def test_colspan_box_row_keeps_its_lines(span):
    """표 전체 폭을 차지하는 한 칸도 테두리 상자다. 글을 열 수만큼 반복하지 않는다."""
    body = _body(
        f"<table><tr><td {span}>"
        "<p>1. 신청기간: 9.1.~9.5.</p><p>2. 방법: 온라인</p>"
        "</td></tr></table>"
    )
    assert body == "1. 신청기간: 9.1.~9.5.\n2. 방법: 온라인"


def test_rowspan_and_colspan_on_one_cell_fill_both():
    body = _body(
        "<table>"
        "<tr><td rowspan='2' colspan='2'>AB</td><td>C</td></tr>"
        "<tr><td>c2</td></tr>"
        "</table>"
    )
    assert body == "AB | AB | C\nAB | AB | c2"


def test_rowspan_in_last_column_fills_next_row():
    body = _body(
        "<table>"
        "<tr><th>구분</th><th>기간</th><th>비고</th></tr>"
        "<tr><td>1차</td><td>9월</td><td rowspan='2'>공통</td></tr>"
        "<tr><td>2차</td><td>10월</td></tr>"
        "</table>"
    )
    assert body == "구분 | 기간 | 비고\n1차 | 9월 | 공통\n2차 | 10월 | 공통"
