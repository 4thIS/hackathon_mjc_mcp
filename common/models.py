"""툴 반환 모델.

반환 타입 어노테이션이 곧 MCP output schema가 되므로,
Field(description=...)이 AI가 읽는 스키마 설명이 된다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 혼잡 단계 경계(이용률 %). 반올림 전 값으로 비교한다.
CONGESTION_NORMAL_FROM = 50
CONGESTION_BUSY_FROM = 80

LIBRARY_NOTE = (
    "measured_at은 우리 서버가 좌석 정보를 조회한 시각이며, 학교가 좌석 수를 갱신한 시각이 아니다. "
    "이 좌석 수치만으로는 도서관이나 열람실이 지금 운영 중인지 알 수 없다."
)


class ReadingRoom(BaseModel):
    name: str = Field(description="열람실 이름")
    total: int = Field(description="총 좌석 수")
    in_use: int = Field(description="현재 사용 중인 좌석 수")
    available: int = Field(description="현재 이용 가능한 잔여 좌석 수")
    usage_rate: float | None = Field(
        default=None,
        description="이용률(%). 사용 중인 좌석 ÷ 총 좌석, 소수 첫째 자리. 총 좌석이 0이면 null.",
    )
    congestion: Literal["여유", "보통", "혼잡"] | None = Field(
        default=None,
        description="혼잡 단계. 이용률 50% 미만 여유, 50% 이상 80% 미만 보통, 80% 이상 혼잡. "
        "총 좌석이 0이면 null.",
    )

    # computed_field는 MCP output schema에 나오지 않아 일반 필드로 두고 검증 뒤에 채운다.
    # 입력값은 무시하고 total·in_use로 다시 계산하므로 옛 캐시에서도 같은 값이 나온다.
    @model_validator(mode="after")
    def _fill_usage(self) -> "ReadingRoom":
        if self.total <= 0:
            self.usage_rate = None
            self.congestion = None
            return self
        rate = self.in_use / self.total * 100
        self.usage_rate = round(rate, 1)
        if rate >= CONGESTION_BUSY_FROM:
            self.congestion = "혼잡"
        elif rate >= CONGESTION_NORMAL_FROM:
            self.congestion = "보통"
        else:
            self.congestion = "여유"
        return self


class LibrarySeatReport(BaseModel):
    rooms: list[ReadingRoom] = Field(description="열람실별 좌석 현황")
    measured_at: datetime = Field(
        description="이 좌석 수치를 실제로 조회한 시각(KST). "
        "실시간 데이터이므로 오래된 값을 현재 값처럼 인용하지 말 것."
    )
    stale_age_min: int | None = Field(
        default=None,
        description="학교 서버 조회에 실패해 캐시된 값을 반환한 경우, "
        "그 값이 몇 분 전 것인지. 실시간 조회에 성공했다면 null.",
    )
    note: str = Field(
        default=LIBRARY_NOTE,
        description="이 응답을 해석할 때 지켜야 할 안내. 사용자에게 답할 때 함께 전할 것.",
    )


class NoticeSummary(BaseModel):
    notice_id: str = Field(
        description="get_notice에 그대로 넘길 식별자. 형식은 내부 구현이므로 해석하지 말 것."
    )
    title: str = Field(description="공지 제목")
    department: str = Field(description="작성 부서")
    posted_on: str = Field(description="작성일 (YYYY-MM-DD)")
    views: int = Field(description="조회수")
    pinned: bool = Field(description="상단 고정 공지 여부")


class NoticeList(BaseModel):
    category: str = Field(description="조회한 게시판 이름")
    notices: list[NoticeSummary] = Field(description="공지 목록. 최신순.")
    stale_age_min: int | None = Field(
        default=None,
        description="학교 서버 조회에 실패해 캐시된 값을 반환한 경우, "
        "그 값이 몇 분 전 것인지. 실시간 조회에 성공했다면 null.",
    )


class NoticeDetail(BaseModel):
    title: str = Field(description="공지 제목")
    department: str = Field(description="작성 부서")
    posted_on: str = Field(description="작성일 (YYYY-MM-DD)")
    views: int = Field(description="조회수")
    body: str = Field(
        description="본문 텍스트. 본문이 이미지로만 작성된 공지는 "
        "그 사실을 알리는 안내 문구가 들어간다."
    )
    body_images: list[str] = Field(
        default_factory=list,
        description="본문에 포함된 이미지의 URL 목록. 본문이 이미지로만 작성된 공지는 "
        "이 링크를 사용자에게 그대로 안내해 직접 보게 할 것. 이미지가 없으면 빈 목록.",
    )
    attachments: list[str] = Field(description="첨부파일 이름 목록")
    truncated: bool = Field(description="본문이 길이 제한으로 잘렸는지 여부")
    source_url: str = Field(
        default="",
        description="이 공지 원문 페이지의 전체 URL. 사용자가 브라우저로 바로 열 수 있다.",
    )
    stale_age_min: int | None = Field(
        default=None,
        description="학교 서버 조회에 실패해 캐시된 값을 반환한 경우, "
        "그 값이 몇 분 전 것인지. 실시간 조회에 성공했다면 null.",
    )


class Department(BaseModel):
    code: str = Field(description="search_courses의 department_code에 그대로 넘길 학과 코드")
    name: str = Field(description="학과명")


class DepartmentList(BaseModel):
    departments: list[Department] = Field(description="sugang에 등록된 전체 학과 목록")


class CourseSummary(BaseModel):
    course_code: str = Field(description="강좌 코드")
    name: str = Field(description="과목명")
    professor: str = Field(description="담당 교수명")
    category: str = Field(description="이수구분 (예: 전공과정, 교양필수)")
    credit: int = Field(description="학점")
    grade: int = Field(description="대상 학년")
    schedule: str = Field(description="강의 시간과 강의실. 교시마다 줄바꿈으로 구분")
    capacity: int = Field(description="정원. 실시간 신청 인원은 제공하지 않는다")
    section: str = Field(description="분반. get_syllabus의 section에 그대로 넘길 것")


class CourseList(BaseModel):
    courses: list[CourseSummary] = Field(description="검색된 개설 강좌 목록")


class SyllabusDetail(BaseModel):
    course_name: str = Field(description="교과목명")
    professor: str = Field(description="담당 교수명")
    category: str = Field(description="이수구분 (예: 통합전공교과)")
    credit: int = Field(description="학점")
    grade_semester: str = Field(description="대상 학년/학기(분반). 원문 표시 그대로")
    overview: str = Field(description="교과목 개요")
    goals: str = Field(description="교과목표")
    content_summary: str = Field(description="교육내용 요약")
    evaluation_methods: list[str] = Field(
        description="평가방법 중 실제로 체크된 항목만(예: ['A.포트폴리오', 'K.구두발표'])"
    )
    source_url: str = Field(
        description="강의계획서 원문(ncsi) 링크. 주차별 계획·교재·장애학생 지원 등 "
        "이 모델에 없는 상세는 여기서 직접 확인하도록 안내할 것"
    )
