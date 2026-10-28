"""툴 반환 모델.

반환 타입 어노테이션이 곧 MCP output schema가 되므로,
Field(description=...)이 AI가 읽는 스키마 설명이 된다.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class ReadingRoom(BaseModel):
    name: str = Field(description="열람실 이름")
    total: int = Field(description="총 좌석 수")
    in_use: int = Field(description="현재 사용 중인 좌석 수")
    available: int = Field(description="현재 이용 가능한 잔여 좌석 수")


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
    code: str = Field(
        description="학과 코드. 학과 코드를 인자로 받는 도구가 있으면 이 값을 그대로 넘길 것. "
        "직접 만들어 내지 말 것."
    )
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
