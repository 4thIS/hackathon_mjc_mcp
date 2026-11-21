"""학교 시스템에 대한 GET 요청 통로.

- 타임아웃을 반드시 건다 (무한 대기 시 MCP 툴이 hang된다)
- 호스트별로 최소 1초 간격을 강제한다 (데이터 수집 원칙)
- 응답은 bytes로 돌려준다. 인코딩 판단은 common.parse의 몫이다.
"""

import threading
import time

import httpx

from common.errors import FetchError

USER_AGENT = (
    "MJC-MCP/0.1 (Myongji College hackathon project; "
    "+https://github.com/4thIS/hachathon_mjc_mcp)"
)
TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=5.0)
MIN_INTERVAL_SEC = 1.0
_ALLOWED_AUTH_HOSTS = frozenset({"sugang.mjc.ac.kr"})
# fetch()는 임의 호스트로 나갈 수 있는 비인증 경로다. 자격증명을 싣는 헤더는
# 여기로 넣을 수 없게 막는다(대소문자 무시 — HTTP 헤더명은 대소문자를 가리지 않는다).
_FORBIDDEN_FETCH_HEADERS = frozenset({"cookie", "authorization"})

_last_request_at: dict[str, float] = {}
# 동기 툴은 스레드풀에서 병렬로 실행되므로, 같은 호스트의 "간격 확인 → 요청 → 기록"이
# 겹치지 않게 호스트별 락으로 묶는다. 락 없이는 동시에 들어온 호출이 모두 간격을
# 지난 것으로 보고 한꺼번에 나간다.
_host_locks: dict[str, threading.Lock] = {}
_host_locks_guard = threading.Lock()


def _host_lock(host: str) -> threading.Lock:
    with _host_locks_guard:
        return _host_locks.setdefault(host, threading.Lock())


def _get_once(
    url: str, params: dict[str, str] | None, headers: dict[str, str] | None
) -> bytes:
    with httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
    ) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
    return response.content


def fetch(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    """GET 요청 후 응답 본문을 bytes로 반환한다.

    실패 시 한 번만 재시도한다. 조회 전용이므로 재시도가 안전하다.
    재시도도 같은 호스트에 대한 연속 요청이므로 대기 시간은 MIN_INTERVAL_SEC를 따른다.
    headers는 기본 User-Agent 위에 덮어써진다.

    이 경로는 세션 쿠키가 필요 없는 요청 전용이라 대상 호스트를 제한하지 않는다.
    그 전제를 관례가 아니라 코드로 지키기 위해, 자격증명을 싣는 헤더
    (Cookie/Authorization)가 headers에 들어오면 요청 자체를 거부한다.
    쿠키가 필요한 요청은 fetch_authenticated()를 쓸 것.
    """
    if headers:
        for name in headers:
            if name.strip().lower() in _FORBIDDEN_FETCH_HEADERS:
                raise FetchError("허용되지 않은 요청 헤더")

    host = httpx.URL(url).host
    with _host_lock(host):
        elapsed = time.monotonic() - _last_request_at.get(host, 0.0)
        if elapsed < MIN_INTERVAL_SEC:
            time.sleep(MIN_INTERVAL_SEC - elapsed)

        try:
            try:
                return _get_once(url, params, headers)
            except httpx.HTTPError:
                time.sleep(MIN_INTERVAL_SEC)
                return _get_once(url, params, headers)
        except httpx.HTTPError as exc:
            # 원문을 담지 않기 위해 타입명만 넘기고 예외 체인을 끊는다.
            raise FetchError(type(exc).__name__) from None
        finally:
            _last_request_at[host] = time.monotonic()


def _request_once(
    url: str,
    *,
    cookies: dict[str, str],
    method: str,
    data: dict[str, str] | None,
    params: dict[str, str] | None,
    headers: dict[str, str] | None,
) -> httpx.Response:
    with httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        cookies=cookies,
    ) as client:
        response = client.request(method, url, params=params, data=data)
        if response.status_code >= 400:
            response.raise_for_status()
    return response


def fetch_authenticated(
    url: str,
    *,
    cookies: dict[str, str],
    method: str = "GET",
    data: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """세션 쿠키가 필요한 요청.

    fetch()와 달리 bytes가 아니라 httpx.Response 전체를 반환한다. 3xx
    리다이렉트는 예외로 취급하지 않고 그대로 반환하므로, 호출자가
    require_active_session()으로 세션 만료 여부를 판단해야 한다.
    네트워크 오류나 4xx/5xx는 fetch()와 동일하게 1회 재시도한다.

    headers는 기본 User-Agent 위에 덮어써진다(예: sugang은 lectList 호출에
    Referer를 요구한다 — 실제 로그인 세션으로 실측 확인됨).

    세션 쿠키를 다루는 요청이 임의 호스트로 나가지 않도록 대상을
    _ALLOWED_AUTH_HOSTS로 제한한다. common.session이 쿠키를
    dict[str, str]로 저장해 domain/path/secure 스코프가 사라지므로,
    이 검증이 없으면 url 인자가 사용자 입력이나 AI가 조합한 값일 때
    세션 쿠키가 의도치 않은 목적지로 샐 수 있다.
    """
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or parsed.host not in _ALLOWED_AUTH_HOSTS:
        raise FetchError("허용되지 않은 요청 대상")

    host = parsed.host
    with _host_lock(host):
        elapsed = time.monotonic() - _last_request_at.get(host, 0.0)
        if elapsed < MIN_INTERVAL_SEC:
            time.sleep(MIN_INTERVAL_SEC - elapsed)

        try:
            try:
                return _request_once(
                    url, cookies=cookies, method=method, data=data, params=params, headers=headers
                )
            except httpx.HTTPError:
                time.sleep(MIN_INTERVAL_SEC)
                return _request_once(
                    url, cookies=cookies, method=method, data=data, params=params, headers=headers
                )
        except httpx.HTTPError as exc:
            raise FetchError(type(exc).__name__) from None
        finally:
            _last_request_at[host] = time.monotonic()
