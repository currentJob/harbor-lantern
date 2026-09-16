"""구운 파일의 바이트 재현성과 요청 예산 상수 (AC-082 · DSN-40 · DSN-47 · 설계서 §16.19).

**여기서 베이커를 실행하지 않는다.** import 하는 것은 `bakery.io` 의 순수 함수들뿐이고,
입력은 전부 이 파일 안의 고정 dict 다(NFR-019 · AC-081).

이 파일이 지키는 명제는 하나다 — *같은 입력과 같은 `--as-of` 면 같은 바이트가 나온다.*
그것이 깨지는 가장 흔한 경로 셋을 각각 센다: dict 순서 · 개행 · 시계(`--as-of` 인자화).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from bakery import io as bakery_io  # noqa: E402  (sys.path 조정 뒤에 와야 한다)

# 고정 가짜 도시 — 실제 굽기 결과의 모양만 본떴다. 값은 아무 응답에서도 오지 않았다.
FAKE_CITY = {
    "schema_version": 1,
    "city_id": "fixtureville",
    "retrieved_at": "2026-09-15",
    "grade": "partial",
    "harvest": {"spot_count": 2, "ko_label_ratio": 0.5, "grade_window": 2},
    "spots": [
        {
            "id": "wd:Q1",
            "wikidata_id": "Q1",
            "name": "첫 번째 장소",
            "name_source": "ko",
            "lat": 48.858296,
            "lng": 2.294479,
            "importance": {"sitelinks": 191},
        },
        {
            "id": "wd:Q2",
            "wikidata_id": "Q2",
            "name": "Second Place",
            "name_source": "en",
            "lat": 48.860611,
            "lng": 2.337644,
            "importance": {"sitelinks": 169},
        },
    ],
}


def test_dumps_is_byte_identical_across_calls() -> None:
    """같은 객체를 두 번 직렬화하면 바이트가 같다 (AC-082)."""
    assert bakery_io.dumps(FAKE_CITY).encode("utf-8") == bakery_io.dumps(FAKE_CITY).encode("utf-8")


def test_dumps_ignores_dict_insertion_order() -> None:
    """키 순서가 다른 두 dict 가 같은 바이트를 낸다 — `sort_keys=True` 가 하는 일이다.

    이것이 없으면 파이프라인의 조립 순서가 바뀔 때마다 이유 없는 diff 가 생기고,
    그 소음 속에서 진짜 변경이 안 보인다.
    """
    forward = {"a": 1, "b": {"x": 1, "y": 2}, "c": [1, 2]}
    backward = {"c": [1, 2], "b": {"y": 2, "x": 1}, "a": 1}
    assert bakery_io.dumps(forward) == bakery_io.dumps(backward)


def test_dumps_keeps_korean_readable_and_ends_with_single_newline() -> None:
    text = bakery_io.dumps({"name": "에펠탑"})
    assert "에펠탑" in text  # ensure_ascii=False — 구운 파일을 사람이 읽는다
    assert text.endswith("}\n")
    assert not text.endswith("\n\n")


def test_written_file_uses_lf_only(tmp_path: Path) -> None:
    """Windows 에서 구워 리눅스 CI 에서 검사한다 — 개행 하나로 AC-082 가 갈리면 안 된다."""
    path = tmp_path / "city.json"
    bakery_io.write_json(path, FAKE_CITY)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw == bakery_io.dumps(FAKE_CITY).encode("utf-8")


def test_rewriting_the_same_document_changes_nothing(tmp_path: Path) -> None:
    """두 번 구운 결과가 바이트로 같다 (AC-082 의 본문)."""
    path = tmp_path / "city.json"
    bakery_io.write_json(path, FAKE_CITY)
    first = path.read_bytes()
    bakery_io.write_json(path, FAKE_CITY)
    assert path.read_bytes() == first


def test_no_clock_is_read_during_serialization() -> None:
    """`retrieved_at` 은 입력에서 오고 직렬화가 만들어 내지 않는다 (함정 F14).

    베이커가 `date.today()` 를 읽으면 기능은 완벽히 동작하고 **재현성만 조용히 사라진다.**
    그래서 날짜를 지운 입력에는 날짜가 없어야 한다.
    """
    without_date = {key: value for key, value in FAKE_CITY.items() if key != "retrieved_at"}
    assert "retrieved_at" not in bakery_io.dumps(without_date)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(48.8582963214, 48.858296), (2.29447851, 2.294479), (-33.8610004, -33.861)],
)
def test_round_coord_is_six_decimals(value: float, expected: float) -> None:
    assert bakery_io.round_coord(value) == expected


def test_round_ratio_is_three_decimals() -> None:
    """등급 판정에 쓰이는 반올림과 같아야 한다 — `guide_grade.grade_city` 도 3자리로 판정한다."""
    assert bakery_io.round_ratio(21 / 25) == 0.84
    assert bakery_io.round_ratio(0.7996) == 0.8


def test_batch_and_interval_constants_stay_within_provider_limits() -> None:
    """공급자 고지 상한 (§16.21 출처표 · 2026-09-15 조회).

    - `wbgetentities` 50개/요청
    - `extracts` `exlimit` 20
    - 요청 간격 1.1초 이상 (준수 UA 봇 상한 200/분 · 우리는 분당 최대 55)
    """
    assert bakery_io.WBGETENTITIES_BATCH <= 50
    assert bakery_io.EXTRACTS_BATCH <= 20
    assert bakery_io.MIN_REQUEST_INTERVAL_S >= 1.1
    assert 60.0 / bakery_io.MIN_REQUEST_INTERVAL_S <= 200
    assert bakery_io.WDQS_TIMEOUT_S < 60.0  # WDQS 공개 엔드포인트 상한보다 먼저 끊는다
    assert len(bakery_io.RETRY_BACKOFF_S) == 3  # 재시도 3회 — **같은 파라미터로만**(함정 F13)
    assert list(bakery_io.RETRY_BACKOFF_S) == sorted(bakery_io.RETRY_BACKOFF_S)


def test_user_agent_carries_a_contact() -> None:
    """미인증 요청 상한은 10/분, 연락처를 밝힌 봇은 200/분이다(2026-09-15 조회)."""
    assert "harbor-lantern" in bakery_io.USER_AGENT
    assert "https://" in bakery_io.USER_AGENT


def test_chunked_respects_the_batch_cap() -> None:
    items = [f"Q{n}" for n in range(1, 121)]
    batches = list(bakery_io.chunked(items, bakery_io.WBGETENTITIES_BATCH))
    assert [len(batch) for batch in batches] == [50, 50, 20]
    assert [qid for batch in batches for qid in batch] == items


def test_cache_key_includes_as_of_and_query_parameters() -> None:
    """대장을 고치면 캐시가 **자동으로 무효화**된다 — 옛 응답이 조용히 재사용되지 않는다."""
    base = {"as_of": "2026-09-15", "stage": "wdqs", "radius_m": 6000, "sitelink_min": 15}
    assert bakery_io.cache_key(base) == bakery_io.cache_key(dict(reversed(list(base.items()))))
    assert bakery_io.cache_key(base) != bakery_io.cache_key({**base, "as_of": "2026-09-16"})
    assert bakery_io.cache_key(base) != bakery_io.cache_key({**base, "radius_m": 3000})
    assert bakery_io.cache_key(base) != bakery_io.cache_key({**base, "sitelink_min": 25})


def test_response_cache_round_trips_without_network(tmp_path: Path) -> None:
    """재개 경로 — 캐시 적중분은 네트워크 없이 지나간다 (§16.18)."""
    cache = bakery_io.ResponseCache(tmp_path)
    assert cache.get("paris", "wdqs", "k1") is None
    cache.put("paris", "wdqs", "k1", {"results": {"bindings": []}})
    assert cache.get("paris", "wdqs", "k1") == {"results": {"bindings": []}}

    reopened = bakery_io.ResponseCache(tmp_path)  # 다른 실행에서 이어 하기
    assert reopened.get("paris", "wdqs", "k1") == {"results": {"bindings": []}}
    assert reopened.hits == 1
    assert (tmp_path / "paris" / "wdqs.json").read_bytes().count(b"\r\n") == 0


def test_offline_client_refuses_to_invent_a_result() -> None:
    """캐시가 비어 있는데 네트워크가 막혀 있으면 **실패한다**.

    조용히 빈 결과를 돌려주면 그 도시가 이유 없이 미달로 기록되고, 미달은 수록에서
    빠진다 — 사라진 이유가 어디에도 남지 않는 경로다.
    """
    client = bakery_io.HttpClient(offline=True)
    with pytest.raises(bakery_io.BakeError):
        client.get_json("wdqs", "https://query.wikidata.org/sparql")
