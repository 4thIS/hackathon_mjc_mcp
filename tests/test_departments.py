import pytest

from tools.departments import DEPARTMENTS, list_departments


def test_known_department_present():
    assert DEPARTMENTS.get("1200204") == "정보통신공학과"


def test_general_education_present():
    assert DEPARTMENTS.get("1201001") == "교양"


def test_at_least_thirty_departments():
    assert len(DEPARTMENTS) >= 30


def test_no_duplicate_codes():
    # dict 자체가 키 중복을 허용하지 않으므로, 원본 리스트 정의 단계에서
    # 중복이 있었다면 조용히 마지막 값으로 덮어써졌을 수 있다. 이를 잡아낸다.
    assert len(DEPARTMENTS) == len(set(DEPARTMENTS))


def test_list_departments_returns_all():
    result = list_departments()
    assert len(result.departments) == len(DEPARTMENTS)
    names = {d.name for d in result.departments}
    assert "정보통신공학과" in names


def test_list_departments_no_login_required():
    """이 함수를 호출하는 데 세션/쿠키 인자가 전혀 없어야 한다."""
    import inspect

    sig = inspect.signature(list_departments)
    assert len(sig.parameters) == 0


def test_tool_description_does_not_point_to_login_only_tool():
    """원격 서버에는 search_courses가 없다. 설명이 그 도구를 가리키면 안 된다."""
    assert "search_courses" not in list_departments.__doc__


def test_code_field_description_does_not_point_to_login_only_tool():
    from common.models import DepartmentList

    props = DepartmentList.model_json_schema()["$defs"]["Department"]["properties"]
    assert props["code"]["description"]
    assert "search_courses" not in props["code"]["description"]
