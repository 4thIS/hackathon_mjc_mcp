"""4개 게시판 실제 응답에 대한 회귀 테스트.

게시글은 매일 바뀌므로 건수·제목 같은 값은 검사하지 않는다.
fixture를 새 응답으로 바꿔도 성립해야 하는 형식과 불변 성질만 검사한다.
"""

import re
from datetime import date
from pathlib import Path

import pytest

from tools.notices import (
    BOARDS,
    MAX_BODY_CHARS,
    _IMAGE_ONLY_BODY,
    parse_notice_detail,
    parse_notice_list,
)

FIXTURES = Path(__file__).parent / "fixtures"
LIST_FIXTURES = {
    "general": "notices_general.html",
    "academic": "notices_academic.html",
    "scholarship": "notices_scholarship.html",
    "job": "notices_job.html",
}
ATTACHMENT_DETAIL = FIXTURES / "notice_detail_attachments.html"
ATTACHMENT_DETAIL_URL = (
    "https://www.mjc.ac.kr/bbs/data/view.do"
    "?menu_idx=208&bbs_mst_idx=BM0000000032&data_idx=BD0000001001"
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# latin-1 같은 단일 바이트 인코딩으로 잘못 읽으면 U+FFFD 없이 글자가 깨진다.
# 학교 공지는 항상 한글을 포함하므로 한글이 사라졌는지로 잡는다.
_HANGUL = re.compile(r"[가-힣]")


def _is_iso_date(value: str) -> bool:
    if not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


@pytest.fixture(params=sorted(LIST_FIXTURES), ids=sorted(LIST_FIXTURES))
def board(request):
    category = request.param
    menu_idx = BOARDS[category][0]
    content = (FIXTURES / LIST_FIXTURES[category]).read_bytes()
    return menu_idx, parse_notice_list(content, menu_idx)


def test_every_board_fixture_parses(board):
    _, notices = board
    assert notices


def test_notice_id_format_and_menu_idx(board):
    menu_idx, notices = board
    pattern = re.compile(rf"{menu_idx}:BM\d+:BD\d+")
    assert all(pattern.fullmatch(n.notice_id) for n in notices)


def test_notice_ids_are_unique(board):
    _, notices = board
    ids = [n.notice_id for n in notices]
    assert len(ids) == len(set(ids))


def test_posted_on_is_valid_iso_date(board):
    _, notices = board
    assert all(_is_iso_date(n.posted_on) for n in notices)


def test_title_and_department_are_present_without_mojibake(board):
    _, notices = board
    assert all(n.title and n.department for n in notices)
    joined = "".join(n.title + n.department for n in notices)
    assert "�" not in joined


def test_titles_contain_hangul(board):
    _, notices = board
    assert _HANGUL.search("".join(n.title for n in notices))


def test_views_are_non_negative(board):
    _, notices = board
    assert all(n.views >= 0 for n in notices)


def test_pinned_notices_come_first(board):
    _, notices = board
    flags = [n.pinned for n in notices]
    assert flags == sorted(flags, reverse=True)


def test_regular_notices_are_newest_first(board):
    _, notices = board
    dates = [n.posted_on for n in notices if not n.pinned]
    assert dates == sorted(dates, reverse=True)


@pytest.fixture
def attachment_detail():
    return parse_notice_detail(ATTACHMENT_DETAIL.read_bytes(), ATTACHMENT_DETAIL_URL)


def test_attachment_detail_metadata(attachment_detail):
    assert attachment_detail.title
    assert attachment_detail.department
    assert _is_iso_date(attachment_detail.posted_on)
    assert attachment_detail.views >= 0


def test_attachment_detail_has_text_body(attachment_detail):
    """텍스트로 작성된 공지는 이미지 전용 안내 문구가 아니라 본문을 돌려준다."""
    assert attachment_detail.body.strip()
    assert attachment_detail.body != _IMAGE_ONLY_BODY
    assert len(attachment_detail.body) <= MAX_BODY_CHARS


def test_attachment_names_are_listed(attachment_detail):
    names = attachment_detail.attachments
    assert names
    assert all(re.search(r"\.\w+$", name) for name in names)
    assert len(names) == len(set(names))


def test_attachment_detail_has_no_mojibake(attachment_detail):
    text = (
        attachment_detail.title
        + attachment_detail.body
        + "".join(attachment_detail.attachments)
    )
    assert "�" not in text


def test_attachment_detail_contains_hangul(attachment_detail):
    assert _HANGUL.search(attachment_detail.title + attachment_detail.body)


def test_attachment_detail_image_urls_are_absolute(attachment_detail):
    assert all(url.startswith("https://") for url in attachment_detail.body_images)
