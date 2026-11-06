"""학사·장학·채용·일반 공지 게시판 조회.

게시판 내부 파라미터(menu_idx, BM/BD 코드)는 AI에게 노출하지 않는다.
AI는 의미 있는 카테고리 이름만 쓰고, 상세 조회에는 목록이 돌려준
불투명 ID를 그대로 넘긴다.
"""

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlencode, urljoin

from bs4 import Comment, NavigableString, Tag
from mcp.types import ToolAnnotations

from common.cache import with_fallback
from common.errors import ParseError, ToolError
from common.http import fetch
from common.models import NoticeDetail, NoticeList, NoticeSummary
from common.parse import parse_html

LIST_URL = "https://www.mjc.ac.kr/bbs/data/list.do"
VIEW_URL = "https://www.mjc.ac.kr/bbs/data/view.do"
MAX_BODY_CHARS = 4000
_IMAGE_ONLY_BODY = (
    "(이 공지의 본문은 이미지로 작성되어 텍스트를 추출할 수 없습니다. "
    "body_images의 이미지 링크를 사용자에게 안내하거나, "
    "source_url에서 원문을 직접 확인하도록 하세요.)"
)

# 카테고리 키 -> (menu_idx, 사람이 읽는 게시판 이름)
BOARDS: dict[str, tuple[int, str]] = {
    "general": (66, "공지사항"),
    "academic": (169, "학사공지"),
    "scholarship": (208, "장학공지"),
    "job": (2617, "채용공지"),
}

NoticeCategory = Literal["general", "academic", "scholarship", "job"]
NoticeSort = Literal["board", "newest", "oldest"]

_FN_VIEW = re.compile(r"fn_view\('(BM\d+)','(BD\d+)'")

# 이 태그의 앞뒤에서만 줄을 바꾼다. 나머지(span, b, font, a 등)는 글자를 이어 붙인다.
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "center", "dd", "details",
        "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav",
        "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot",
        "th", "thead", "ul",
    }
)
_SKIP_TAGS = frozenset({"script", "style"})


def parse_notice_list(content: bytes, menu_idx: int) -> list[NoticeSummary]:
    soup = parse_html(content)
    table = soup.select_one("table.board_list")
    if table is None:
        raise ParseError("공지 목록")

    notices: list[NoticeSummary] = []
    for row in table.select("tr"):
        link = row.select_one("td.cell_type01 a[href*='fn_view']")
        if link is None:
            continue
        match = _FN_VIEW.search(link.get("href", ""))
        if match is None:
            continue

        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        # 첫 칸이 행마다 번호/공지 아이콘으로 달라지므로 뒤에서부터 센다.
        views_text = cells[-1].get_text(strip=True)
        # 첨부가 있는 글만 첨부 칸에 아이콘 이미지가 들어 있다.
        has_attachment = cells[-4].find("img") is not None if len(cells) >= 4 else False
        notices.append(
            NoticeSummary(
                notice_id=f"{menu_idx}:{match.group(1)}:{match.group(2)}",
                title=link.get_text(strip=True),
                department=cells[-3].get_text(strip=True),
                posted_on=cells[-2].get_text(strip=True),
                views=int(views_text) if views_text.isdigit() else 0,
                pinned="cell_notice" in (row.get("class") or []),
                has_attachment=has_attachment,
            )
        )

    return notices


def _span(cell: Tag, name: str, limit: int) -> int:
    """칸의 rowspan·colspan 값. 숫자가 아니거나 1보다 작으면 1로 본다."""
    try:
        value = int(cell.get(name, "1"))
    except (TypeError, ValueError):
        return 1
    return min(max(value, 1), limit)


def _flatten_lines(element: Tag) -> list[str]:
    """본문을 줄 목록으로 만든다.

    편집기가 글자를 span 조각으로 잘게 나눠 저장하므로, 블록 경계와 <br>에서만
    줄을 바꾸고 원문의 공백·줄바꿈은 공백 하나로 접는다. 표는 행 하나를
    "칸 | 칸" 한 줄로 남겨 칸의 순서를 잃지 않게 한다. 합쳐진 칸(rowspan·colspan)은
    걸친 행·열마다 다시 채워 행마다 열 위치가 머리 행과 같게 한다. 칸이 하나뿐인
    행은 칸 안의 줄을 그대로 둔다.
    """
    lines: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        line = " ".join("".join(buffer).split())
        buffer.clear()
        if line:
            lines.append(line)

    def add_row(row: Tag, carried: dict[int, tuple[int, list[str]]]) -> None:
        # carried: 열 번호 -> (아직 채울 행 수, 위 행에서 rowspan으로 내려온 칸의 줄)
        cells: list[list[str]] = []

        def take_carried() -> None:
            left, text = carried.pop(len(cells))
            if left > 1:
                carried[len(cells)] = (left - 1, text)
            cells.append(text)

        for cell in row.find_all(["td", "th"], recursive=False):
            while len(cells) in carried:
                take_carried()
            text = _flatten_lines(cell)
            rowspan = _span(cell, "rowspan", 65534)
            for _ in range(_span(cell, "colspan", 1000)):
                if rowspan > 1:
                    carried[len(cells)] = (rowspan - 1, text)
                cells.append(text)
        while carried and max(carried) >= len(cells):
            if len(cells) in carried:
                take_carried()
            else:
                cells.append([])

        if len(cells) == 1:
            # 칸이 하나뿐인 행은 표가 아니라 테두리 상자다. 칸 안의 줄을 그대로 둔다.
            lines.extend(cells[0])
        elif any(cells):
            lines.append(" | ".join(" ".join(cell) for cell in cells))

    def walk_table(table: Tag) -> None:
        # rowspan은 같은 표 안에서만 이어진다. 다음 표로 넘어가지 않게 표마다 새로 센다.
        carried: dict[int, tuple[int, list[str]]] = {}
        for child in table.children:
            if isinstance(child, Tag) and child.name in ("thead", "tbody", "tfoot"):
                for part in child.children:
                    if isinstance(part, Tag) and part.name == "tr":
                        add_row(part, carried)
                    else:
                        walk(part)
                        flush()
            elif isinstance(child, Tag) and child.name == "tr":
                add_row(child, carried)
            else:
                walk(child)
                flush()

    def walk(node) -> None:
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            buffer.append(str(node))
            return
        if not isinstance(node, Tag) or node.name in _SKIP_TAGS:
            return
        if node.name == "br":
            flush()
        elif node.name == "table":
            flush()
            walk_table(node)
        elif node.name == "tr":
            flush()
            add_row(node, {})
        elif node.name in _BLOCK_TAGS:
            flush()
            for child in node.children:
                walk(child)
            flush()
        else:
            for child in node.children:
                walk(child)

    for child in element.children:
        walk(child)
    flush()
    return lines


def _fetch_list(menu_idx: int) -> dict:
    notices = parse_notice_list(
        fetch(LIST_URL, params={"menu_idx": str(menu_idx)}), menu_idx
    )
    return {"notices": [n.model_dump(mode="json") for n in notices]}


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


def search_notices(
    category: NoticeCategory = "academic",
    limit: int = 10,
    since: str | None = None,
    until: str | None = None,
    sort: NoticeSort = "board",
    exclude_pinned: bool = False,
) -> NoticeList:
    """명지전문대 공지 게시판 첫 쪽의 글 목록을 조회한다.

    category: general(일반 공지사항), academic(학사), scholarship(장학), job(채용)
    since, until: 작성일 범위(YYYY-MM-DD). 두 날짜 모두 당일을 포함한다.
                  since가 until보다 늦으면 오류.
                  게시판 첫 쪽(상단 고정 글 + 최신 일반 글)에 있는 글만 거른다.
                  첫 쪽에서 가장 오래된 일반 글의 작성일이 기준이다(exclude_pinned=true,
                  sort=oldest, limit=1로 확인). 기준일 이전 날짜가 기간에 하루라도
                  포함되면 결과가 비거나 일부만 나오므로, 결과를 그 기간의 전체 공지로
                  단정하지 말 것.
    sort: board(게시판 순서 — 상단 고정 글이 먼저, 그다음 일반 글 최신순),
          newest(작성일 최신순), oldest(작성일 오래된순)
    exclude_pinned: true면 상단 고정 글을 뺀다.
    필터·정렬을 적용한 뒤 limit개까지 돌려준다.
    본문 전문이 필요하면 결과의 notice_id를 get_notice에 넘긴다.
    """
    if category not in BOARDS:
        raise ParseError(f"알 수 없는 게시판 '{category}'")
    _check_date("since", since)
    _check_date("until", until)
    if since is not None and until is not None and since > until:
        raise ToolError(
            "since가 until보다 늦습니다. 두 날짜의 순서를 바꿔 다시 요청해 주세요."
        )
    menu_idx, board_name = BOARDS[category]

    data, stale_age_min = with_fallback(
        f"notices_{category}", lambda: _fetch_list(menu_idx)
    )
    notices = [NoticeSummary.model_validate(n) for n in data["notices"]]

    # 필터·정렬은 캐시에서 읽은 값에도 똑같이 적용되도록 조회 뒤에 한다.
    if exclude_pinned:
        notices = [n for n in notices if not n.pinned]
    if since is not None:
        notices = [n for n in notices if n.posted_on >= since]
    if until is not None:
        notices = [n for n in notices if n.posted_on <= until]
    if sort != "board":
        notices.sort(key=lambda n: n.posted_on, reverse=sort == "newest")

    return NoticeList(
        category=board_name,
        notices=notices[: max(1, limit)],
        stale_age_min=stale_age_min,
    )


def parse_notice_detail(content: bytes, source_url: str) -> NoticeDetail:
    soup = parse_html(content)
    view = soup.select_one("div.board_view")
    if view is None:
        raise ParseError("공지 본문")

    title_element = view.select_one("h2.tit")

    # 작성자/조회수/날짜는 th-td 쌍으로 들어 있다. 순서에 의존하지 않고 라벨로 찾는다.
    info: dict[str, str] = {}
    for header in view.select("table.tbl_data th"):
        cell = header.find_next_sibling("td")
        if cell is not None:
            info[header.get_text(strip=True)] = cell.get_text(strip=True)

    memo = view.select_one("#divMemo")
    try:
        body = "\n".join(_flatten_lines(memo)) if memo is not None else ""
    except RecursionError:
        # 태그가 재귀 한도를 넘게 겹친 본문. ToolError 경계 안에 머물러야
        # get_notice의 캐시 폴백이 동작한다.
        raise ParseError("공지 본문") from None
    truncated = len(body) > MAX_BODY_CHARS
    body = body[:MAX_BODY_CHARS] if body else _IMAGE_ONLY_BODY

    # 상대 경로로 나오는 경우가 있어 원문 URL 기준으로 절대 URL을 만든다.
    body_images = [
        urljoin(source_url, src)
        for image in (memo.select("img") if memo is not None else [])
        if (src := image.get("src", "").strip())
    ]

    attachments = [
        link.get_text(strip=True)
        for link in view.select("a[href*='fn_egov_downFile']")
        if link.get_text(strip=True)
    ]

    views_text = info.get("조회수", "")
    return NoticeDetail(
        title=title_element.get_text(strip=True) if title_element else "",
        department=info.get("작성자", ""),
        posted_on=info.get("날짜", ""),
        views=int(views_text) if views_text.isdigit() else 0,
        body=body,
        body_images=body_images,
        attachments=attachments,
        truncated=truncated,
        source_url=source_url,
    )


def _fetch_detail(menu_idx: str, bbs_mst_idx: str, data_idx: str) -> dict:
    params = {
        "menu_idx": menu_idx,
        "bbs_mst_idx": bbs_mst_idx,
        "data_idx": data_idx,
    }
    content = fetch(VIEW_URL, params=params)
    source_url = f"{VIEW_URL}?{urlencode(params)}"
    return parse_notice_detail(content, source_url).model_dump(mode="json")


def get_notice(notice_id: str) -> NoticeDetail:
    """공지 한 건의 본문 전문과 첨부파일 목록을 조회한다.

    notice_id는 반드시 search_notices가 돌려준 값을 그대로 넘겨야 한다.
    사람이 임의로 만들어낼 수 있는 형식이 아니다.
    """
    parts = notice_id.split(":")
    if len(parts) != 3 or not parts[0].isdigit():
        raise ParseError("공지 식별자 형식")
    menu_idx, bbs_mst_idx, data_idx = parts

    data, stale_age_min = with_fallback(
        f"notice_{data_idx}",
        lambda: _fetch_detail(menu_idx, bbs_mst_idx, data_idx),
    )
    detail = NoticeDetail.model_validate(data)
    detail.stale_age_min = stale_age_min
    return detail


def register(mcp) -> None:
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=True)
    mcp.tool(annotations=read_only)(search_notices)
    mcp.tool(annotations=read_only)(get_notice)
