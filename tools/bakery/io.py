"""결정론 직렬화 · 응답 캐시 · HTTP 배선 (DSN-40 · DSN-47 · 설계서 §16.11 · §16.18).

이 모듈이 지키는 것은 하나다: **같은 입력·같은 `--as-of` 면 같은 바이트가 나온다**(NFR-020).
그래서 여기에는 `date.today()` 도 `random` 도 `set` 순회도 없다.

`dumps`/`write_json` 만 순수 함수이고 나머지는 I/O 다. 테스트가 import 하는 것은
순수 함수 쪽뿐이다(§16.19 · AC-082) — HTTP 는 테스트에서 실행하지 않는다(AC-081).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

__all__ = [
    "EXTRACTS_BATCH",
    "GEOSEARCH_TIMEOUT_S",
    "HTTP_TIMEOUT_S",
    "MIN_REQUEST_INTERVAL_S",
    "RETRY_BACKOFF_S",
    "THROTTLED_BACKOFF_S",
    "USER_AGENT",
    "WBGETENTITIES_BATCH",
    "BakeError",
    "HttpClient",
    "ResponseCache",
    "cache_key",
    "chunked",
    "dumps",
    "read_json",
    "round_coord",
    "round_ratio",
    "validate_denylist",
    "write_json",
]

# ─────────────────────────────────────────────────────────────────────────
# 공급자 고지에서 온 상수 (§16.21 출처표)
# ─────────────────────────────────────────────────────────────────────────
# 연락처를 포함한 고정 User-Agent. 미인증 요청 상한이 10/분인데 준수 UA 봇은 200/분이다
# (https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits · 2026-09-15 조회).
# 우리는 요청 간격 1.1초이므로 분당 최대 55 — 여유가 크다.
USER_AGENT = "harbor-lantern-baker/1.0 (+https://github.com/currentJob/harbor-lantern)"

# 요청 간 최소 간격. `tools/resolve_curated.py` 의 선례와 같은 값이다.
MIN_REQUEST_INTERVAL_S = 1.1

# `wbgetentities` 한 번에 50개 (공급자 고지).
WBGETENTITIES_BATCH = 50
# `extracts` 의 `exlimit` 최대 20. 다건 요청에는 `exintro` 가 필수다 (공급자 고지).
EXTRACTS_BATCH = 20

# 1단계 geosearch 는 파리 실측 1.0초다(2026-09-16). 30초를 넘기면 그것은 느린 것이 아니라
# 고장이므로 먼저 끊고 같은 파라미터로 재시도한다 — 이 자리에 있던 55초는 WDQS 의 60초
# 상한을 피하려던 값이고, WDQS 는 §16.4 v1.5 에서 통째로 걷어냈다.
GEOSEARCH_TIMEOUT_S = 30.0
HTTP_TIMEOUT_S = 60.0
# 지수 백오프. **파라미터는 바꾸지 않는다** — 하한을 올려 재시도하면 수확량이 바뀌고,
# 수확량이 바뀌면 등급이 바뀐다(함정 F13 · 도쿄 32건 → 10건, 실측 2026-09-15).
RETRY_BACKOFF_S = (5.0, 15.0, 45.0)

# 429 를 받았을 때의 최소 대기. 공개 엔드포인트가 이 클라이언트를 조이기 시작했다는
# 뜻이므로 초 단위로 다시 두드리지 않는다. 서버가 `Retry-After` 를 주면 그쪽을 쓴다.
THROTTLED_BACKOFF_S = 120.0


def _retry_after_seconds(response: Any) -> float | None:
    """`Retry-After` 를 초로. 초 단위 정수만 읽고, HTTP-date 형식은 읽지 않는다.

    날짜 형식을 해석하려면 서버 시계와 우리 시계의 차를 믿어야 하는데, 틀리면
    **더 짧게** 기다리는 쪽으로 틀린다. 못 읽으면 `None` 을 돌려 호출부의 기본 대기를
    쓰게 하는 편이 안전하다.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        return None
    return seconds if 0 < seconds <= 3600 else None


class BakeError(RuntimeError):
    """굽기를 멈춰야 하는 실패. 메시지에 도시와 사유를 담는다 — 조용히 넘어가지 않는다."""


# ─────────────────────────────────────────────────────────────────────────
# 결정론 직렬화 (DSN-40 · AC-082)
# ─────────────────────────────────────────────────────────────────────────
def dumps(obj: Any) -> str:
    """구운 파일의 유일한 직렬화 경로다 (§16.11 의 표 그대로).

    `sort_keys=True` 가 dict 삽입 순서를 지우고, `indent=1` 이 diff 를 사람이 읽을 수
    있게 하며, 끝의 개행 하나가 POSIX 텍스트 파일 관습을 지킨다. 세 가지가 함께
    "같은 입력 → 같은 바이트"를 만든다.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def write_json(path: Path, obj: Any) -> int:
    """`dumps` 결과를 UTF-8 · LF 로 쓴다. 쓴 바이트 수를 돌려준다.

    `newline="\\n"` 을 명시하는 이유: Windows 에서 기본값으로 열면 `\\r\\n` 이 섞여
    **같은 입력에 다른 바이트**가 나온다. 이 프로젝트는 Windows 에서 굽고 리눅스 CI 에서
    검사한다 — 개행 하나로 AC-082 가 환경마다 다르게 판정되는 것을 막는다.
    """
    text = dumps(obj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return len(text.encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def round_coord(value: float) -> float:
    """좌표는 소수 6자리 고정. 약 0.1m 해상도이고, 그 아래는 출처가 보증하지 않는다."""
    return round(float(value), 6)


def round_ratio(value: float) -> float:
    """비율은 소수 3자리 고정. `guide_grade.grade_city` 가 판정에 쓰는 것과 같은 반올림이다."""
    return round(float(value), 3)


def chunked(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    """배치 상한을 지켜 자른다. 상한은 공급자가 고지한 값이고 우리가 정하는 값이 아니다."""
    if size < 1:
        raise ValueError("batch size must be >= 1")
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def cache_key(parts: Mapping[str, Any]) -> str:
    """캐시 키. `--as-of` 와 질의 파라미터를 전부 넣는다.

    대장(`city-registry.json`)을 고치면 키가 달라져 **자동으로 무효화**된다. 파라미터를
    키에서 빼면 반경을 바꾸고 다시 구웠을 때 옛 응답이 조용히 재사용된다.
    """
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


# ─────────────────────────────────────────────────────────────────────────
# 응답 캐시 (재개 · NFR-020)
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class ResponseCache:
    """`.local/bake-cache/{city_id}/{stage}.json` — 원문 응답 보관소.

    `.gitignore` 대상이다. 원본 응답은 커밋하지 않는다(R9 저장소 부피) — 커밋되는 것은
    **구운 결과**이고, 캐시는 중단된 굽기를 이어 하기 위한 편의일 뿐이다.
    """

    root: Path
    hits: int = 0
    misses: int = 0
    _loaded: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict, repr=False)

    def _path(self, city_id: str, stage: str) -> Path:
        return self.root / city_id / f"{stage}.json"

    def _bucket(self, city_id: str, stage: str) -> dict[str, Any]:
        key = (city_id, stage)
        if key not in self._loaded:
            path = self._path(city_id, stage)
            if path.exists():
                try:
                    self._loaded[key] = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    # 깨진 캐시는 없는 것으로 친다. 캐시는 편의이지 진실이 아니다.
                    self._loaded[key] = {}
            else:
                self._loaded[key] = {}
        return self._loaded[key]

    def get(self, city_id: str, stage: str, key: str) -> Any | None:
        value = self._bucket(city_id, stage).get(key)
        if value is None:
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, city_id: str, stage: str, key: str, payload: Any) -> None:
        bucket = self._bucket(city_id, stage)
        bucket[key] = payload
        path = self._path(city_id, stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(bucket, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
            encoding="utf-8",
            newline="\n",
        )


# ─────────────────────────────────────────────────────────────────────────
# HTTP 배선 (요청 간격 · 재시도 · 예산 집계)
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class HttpClient:
    """공급자 고지를 지키는 최소한의 클라이언트.

    - 요청 간 최소 간격 `MIN_REQUEST_INTERVAL_S`
    - 재시도는 **같은 파라미터로** 최대 3회, 지수 백오프(5·15·45초)
    - 요청 수를 단계별로 센다 — 보고서의 `requests` 가 예산표(§16.18)와 대조된다

    `offline=True` 면 네트워크를 쓰지 않는다. 캐시가 비어 있는데 호출되면 `BakeError` 다 —
    조용히 빈 결과를 돌려주면 그 도시가 이유 없이 미달로 기록된다.
    """

    offline: bool = False
    sleep: Any = time.sleep
    requests: dict[str, int] = field(default_factory=dict)
    _client: httpx.Client | None = field(default=None, repr=False)
    _last_call: float = field(default=0.0, repr=False)

    def __enter__(self) -> HttpClient:
        if not self.offline:
            self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def total_requests(self) -> int:
        return sum(self.requests.values())

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < MIN_REQUEST_INTERVAL_S:
            self.sleep(MIN_REQUEST_INTERVAL_S - elapsed)
        self._last_call = time.monotonic()

    def get_json(
        self,
        stage: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = HTTP_TIMEOUT_S,
    ) -> Any:
        return self._request_json(stage, "GET", url, params=params, data=None, headers=headers, timeout=timeout)

    def post_json(
        self,
        stage: str,
        url: str,
        *,
        data: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
        timeout: float = HTTP_TIMEOUT_S,
    ) -> Any:
        return self._request_json(stage, "POST", url, params=None, data=data, headers=headers, timeout=timeout)

    def _request_json(
        self,
        stage: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        data: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
        timeout: float,
    ) -> Any:
        if self.offline or self._client is None:
            raise BakeError(f"offline 모드인데 {stage} 요청이 필요하다 (캐시 미적중: {url})")

        last_error: Exception | None = None
        override: float | None = None  # 서버가 직접 지시한 대기(Retry-After)
        for attempt, backoff in enumerate((0.0, *RETRY_BACKOFF_S)):
            wait = backoff if override is None else max(backoff, override)
            override = None
            if wait:
                self.sleep(wait)
            self._throttle()
            self.requests[stage] = self.requests.get(stage, 0) + 1
            try:
                response = self._client.request(
                    method, url, params=params, data=data, headers=dict(headers or {}), timeout=timeout
                )
                if response.status_code >= 500 or response.status_code == 429:
                    # 429 는 "잠깐 기다려"가 아니라 "그만 두드려"다. 고정 백오프로 계속
                    # 두드리면 throttle 창이 오히려 길어진다 — 실측에서 겪었다(정찰 중
                    # 연속 질의로 WDQS 가 429 를 주기 시작하자, 몇 분 전 57.9초에 성공한
                    # 질의가 65.9초 504 로 바뀌었다. 그 상태에서 잰 수치는 질의 모양이
                    # 아니라 내 요청량을 잰 것이다).
                    override = _retry_after_seconds(response) or (
                        THROTTLED_BACKOFF_S if response.status_code == 429 else None
                    )
                    raise httpx.HTTPStatusError(
                        f"{response.status_code} {response.reason_phrase}", request=response.request, response=response
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, json.JSONDecodeError) as exc:  # noqa: PERF203 - 재시도 자체가 목적이다
                last_error = exc
                if attempt >= len(RETRY_BACKOFF_S):
                    break
        raise BakeError(f"{stage} 요청이 {len(RETRY_BACKOFF_S) + 1}회 모두 실패했다: {last_error}")

    def cached_json(
        self,
        cache: ResponseCache,
        city_id: str,
        stage: str,
        key: str,
        fetch: Any,
    ) -> Any:
        """캐시 적중이면 네트워크 없이 지나간다. 미적중이면 `fetch()` 를 부르고 저장한다."""
        cached = cache.get(city_id, stage, key)
        if cached is not None:
            return cached
        payload = fetch()
        cache.put(city_id, stage, key, payload)
        return payload


def validate_denylist(doc: Mapping[str, Any]) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """제외 목록을 읽고 **근거 없는 제외를 찾아낸다** (DSN-50 · §16.9.3 · AC-084 자기검사 3).

    순수 함수다 — 파일 읽기는 호출자가 하고 여기서는 판정만 한다. 그래야 "reason 이 비면
    실패한다"를 고정 입력으로 테스트할 수 있다.

    근거 없는 제외는 다음 사람이 되돌릴 수도 유지할 수도 없다. 추천·팁에 요구하는 규칙을
    **우리 자신의 판단에도** 적용한다.
    """
    cleaned: dict[str, list[dict[str, str]]] = {}
    problems: list[str] = []
    cities = doc.get("cities", {})
    if not isinstance(cities, Mapping):
        return {}, ["cities 가 객체가 아니다"]
    for city_id, entries in sorted(cities.items()):
        rows: list[dict[str, str]] = []
        if not isinstance(entries, list):
            problems.append(f"{city_id}: 목록이 아니다")
            continue
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, Mapping):
                problems.append(f"{city_id}: 항목이 객체가 아니다")
                continue
            qid = str(entry.get("qid", "")).strip()
            reason = str(entry.get("reason", "")).strip()
            if not qid:
                problems.append(f"{city_id}: qid 가 비어 있다")
                continue
            if not reason:
                problems.append(f"{city_id}/{qid}: reason 이 비어 있다")
                continue
            if qid in seen:
                problems.append(f"{city_id}/{qid}: 같은 QID 가 두 번 적혀 있다")
                continue
            seen.add(qid)
            rows.append({"qid": qid, "name": str(entry.get("name", "")), "reason": reason})
        cleaned[str(city_id)] = rows
    return cleaned, problems


def merge_counts(target: dict[str, int], source: Mapping[str, int]) -> dict[str, int]:
    """요청 수 집계 합치기. 보고서가 도시별·전체 예산을 함께 적을 수 있게 한다."""
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value)
    return target


def sorted_unique(values: Iterable[str]) -> list[str]:
    """집합 순회를 파일에 남기지 않기 위한 정렬 유니크. `set` 순서는 실행마다 다를 수 있다."""
    return sorted(set(values))
