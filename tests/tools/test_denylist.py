"""사람의 검토 단계 — 제외 목록과 베이커 입력 파일들의 계약 (DSN-50 · DSN-32 · DSN-34).

검증 대상은 **커밋된 데이터 파일**(`tools/*.json`)과 `bakery.io.validate_denylist`
순수 함수다. 베이커는 실행하지 않는다(NFR-019 · AC-081).

여기서 지키는 것 셋:
1. 근거 없는 제외는 **실패한다** — 근거 없는 제외는 다음 사람이 되돌릴 수도 유지할 수도 없다.
2. 제외는 **항목 단위(QID)** 다. 분류 단위 제외는 `wikidata-classes.json` 이 맡는다(R7 · 콜로세움).
3. 제외는 **등급 평가 집합보다 먼저** 적용된다 — 제외분이 상위 25를 차지하면 등급까지 왜곡된다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harbor_lantern.domain.guide_grade import GRADE_WINDOW, grade_city, select_spots

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from bakery import io as bakery_io  # noqa: E402  (sys.path 조정 뒤에 와야 한다)

DENYLIST = json.loads((TOOLS_DIR / "city-denylist.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((TOOLS_DIR / "city-registry.json").read_text(encoding="utf-8"))
CLASSES = json.loads((TOOLS_DIR / "wikidata-classes.json").read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────
# 1. 근거 없는 제외는 실패한다
# ─────────────────────────────────────────────────────────────────────────
def test_committed_denylist_is_valid() -> None:
    cleaned, problems = bakery_io.validate_denylist(DENYLIST)
    assert problems == []
    assert isinstance(cleaned, dict)


def test_empty_reason_is_rejected() -> None:
    """이 한 줄이 DSN-50 의 전부다 — 근거 없는 제외는 굽기를 멈춘다."""
    doc = {"cities": {"tokyo": [{"qid": "Q123", "name": "어떤 건물", "reason": "  "}]}}
    cleaned, problems = bakery_io.validate_denylist(doc)
    assert problems and "reason" in problems[0]
    assert cleaned["tokyo"] == []


def test_missing_reason_field_is_rejected() -> None:
    doc = {"cities": {"tokyo": [{"qid": "Q123", "name": "어떤 건물"}]}}
    _cleaned, problems = bakery_io.validate_denylist(doc)
    assert problems


def test_entry_with_reason_is_kept() -> None:
    doc = {
        "cities": {
            "tokyo": [
                {
                    "qid": "Q11565431",
                    "name": "모드 학원 코쿤 타워",
                    "reason": "전문학교 건물. 건축으로 언어판 수가 높을 뿐 방문 대상이 아니다. 2026-09-15 검토.",
                }
            ]
        }
    }
    cleaned, problems = bakery_io.validate_denylist(doc)
    assert problems == []
    assert [entry["qid"] for entry in cleaned["tokyo"]] == ["Q11565431"]


def test_duplicate_qid_is_reported() -> None:
    """같은 QID 를 두 번 적으면 제외 건수가 실제보다 크게 기록된다 — 지표가 조용히 틀어진다."""
    doc = {
        "cities": {
            "tokyo": [
                {"qid": "Q1", "reason": "사유 1"},
                {"qid": "Q1", "reason": "사유 2"},
            ]
        }
    }
    cleaned, problems = bakery_io.validate_denylist(doc)
    assert problems
    assert len(cleaned["tokyo"]) == 1


def test_denylist_has_the_shape_the_baker_reads() -> None:
    assert DENYLIST["schema_version"] == 1
    assert isinstance(DENYLIST["cities"], dict)
    assert DENYLIST["why_this_exists"]  # 왜 이 단계가 있는지가 파일 안에 남아 있어야 한다


# ─────────────────────────────────────────────────────────────────────────
# 2. 제외는 항목 단위다 — 분류 규칙은 따로 산다
# ─────────────────────────────────────────────────────────────────────────
def test_exclusion_is_flat_and_allow_roots_are_closed() -> None:
    """제외는 평면 `P31` 목록이고, 허용 뿌리는 `category` 로 쓰이는 닫힌 집합이다.

    하위분류까지 제외했더니 로마에서 콜로세움이 사라졌다(실측) — 그 실패를 구조가
    막으려면 제외 목록이 평면 QID 집합이어야 한다.
    """
    assert set(CLASSES["exclude_flat"]) & set(CLASSES["allow_flat"]) == set()
    roots = set(CLASSES["roots"])
    assert set(CLASSES["allow_flat"].values()) <= roots
    assert set(CLASSES["allow_root"].values()) <= roots
    for qid in (*CLASSES["exclude_flat"], *CLASSES["allow_flat"], *CLASSES["allow_root"]):
        assert qid.startswith("Q") and qid[1:].isdigit()


def test_every_allow_root_is_classifiable_on_its_own() -> None:
    """뿌리 자신이 직접 `P31` 로 붙은 항목도 통과해야 한다.

    원래 이 검사는 "모든 뿌리가 `allow_flat` 에도 있어야 한다"는 **데이터 중복**을 요구했다.
    그것은 `classify()` 의 승급이 부모만 보던 시절의 우회책이었다 — 그 구멍으로 다낭의
    다리 4·강 2 가 사라졌다. 지금은 분류기가 규칙 2-b(허용 뿌리 자신)로 직접 처리하므로
    목록을 두 곳에 맞춰 둘 이유가 없다. 그래서 검사 대상을 **목록에서 동작으로** 옮긴다 —
    앞으로 뿌리를 추가하는 사람은 아무것도 기억하지 않아도 된다.
    """
    from harbor_lantern.domain.guide_taxonomy import Taxonomy, classify

    taxonomy = Taxonomy(
        exclude_flat=frozenset(CLASSES["exclude_flat"]),
        allow_flat=CLASSES["allow_flat"],
        allow_root=CLASSES["allow_root"],
    )
    unreachable = [
        qid for qid in CLASSES["allow_root"]
        if classify([qid], {}, taxonomy).decision != "accept"
    ]
    assert not unreachable, f"조상 캐시 없이 분류되지 않는 뿌리: {unreachable}"


def test_classify_accepts_a_root_used_as_a_direct_class() -> None:
    """위 규칙을 규칙이 아니라 **판정으로** 확인한다 — 강(Q4022)이 직접 P31 인 항목."""
    from harbor_lantern.domain.guide_taxonomy import Taxonomy, classify

    taxonomy = Taxonomy(
        exclude_flat=frozenset(CLASSES["exclude_flat"]),
        allow_flat=dict(CLASSES["allow_flat"]),
        allow_root=dict(CLASSES["allow_root"]),
    )
    verdict = classify(["Q4022"], {}, taxonomy)
    assert verdict.decision == "accept"
    assert verdict.root == "river"


def test_classes_file_is_the_single_source_of_truth() -> None:
    """도메인의 `DEFAULT_TAXONOMY` 는 예시다. 굽기가 읽는 것은 이 파일이고, 그래서 더 넓다."""
    from harbor_lantern.domain.guide_taxonomy import DEFAULT_TAXONOMY

    assert set(DEFAULT_TAXONOMY.exclude_flat) <= set(CLASSES["exclude_flat"])
    assert set(DEFAULT_TAXONOMY.allow_root) <= set(CLASSES["allow_root"])


def test_ancestry_cache_is_committed_even_when_empty() -> None:
    """승급 캐시는 굽기 중 채워지고 커밋된다 — 커밋된 캐시가 같은 입력에 같은 판정을 보장한다."""
    assert isinstance(CLASSES["ancestry_cache"], dict)
    for qid, parents in CLASSES["ancestry_cache"].items():
        assert qid.startswith("Q")
        assert isinstance(parents, list)


# ─────────────────────────────────────────────────────────────────────────
# 3. 제외는 등급 평가 집합보다 먼저 적용된다
# ─────────────────────────────────────────────────────────────────────────
def _spot(qid: str, sitelinks: int, source: str) -> dict[str, object]:
    return {
        "id": f"wd:{qid}",
        "wikidata_id": qid,
        "name": f"장소 {qid}",
        "name_source": source,
        "importance": {"sitelinks": sitelinks},
    }


def test_denylisted_noise_would_change_the_grade_if_applied_late() -> None:
    """상위를 차지한 잡음 6건을 **선별 전에** 빼면 등급이 달라진다 (§16.9.3 의 순서 규칙).

    고정 입력: 언어판 수가 가장 높은 6건이 한국어 라벨 없는 잡음이고 나머지 25건은 한국어다.
    잡음을 먼저 빼면 상위 25가 전부 한국어라 `full`, 남겨 두면 비율이 깎여 `partial` 이다.
    """
    noise = [_spot(f"Q9{n}", 500 - n, "en") for n in range(6)]
    good = [_spot(f"Q1{n:02d}", 100 - n, "ko") for n in range(25)]
    harvested = [*noise, *good]

    late = grade_city(harvested)  # 제외를 나중에 적용한 세계
    assert late.grade == "partial"
    assert late.ko_label_ratio < 0.80

    deny = {spot["wikidata_id"] for spot in noise}
    early = grade_city([spot for spot in harvested if spot["wikidata_id"] not in deny])
    assert early.grade == "full"
    assert early.ko_label_ratio == 1.0
    assert early.spot_count == 25


def test_denylist_removes_items_from_the_grade_window_first() -> None:
    """제외분은 상위 `GRADE_WINDOW` 안에서 사라진다 — 꼬리로 밀려나는 것이 아니다."""
    noise = _spot("Q999", 9999, "en")
    good = [_spot(f"Q1{n:02d}", 100 - n, "ko") for n in range(30)]
    window_with_noise = select_spots([noise, *good], limit=GRADE_WINDOW)
    assert window_with_noise[0]["wikidata_id"] == "Q999"

    window_without = select_spots(good, limit=GRADE_WINDOW)
    assert all(spot["wikidata_id"] != "Q999" for spot in window_without)
    assert len(window_without) == GRADE_WINDOW


# ─────────────────────────────────────────────────────────────────────────
# 대장 — 굽기의 유일한 입력 (DSN-32)
# ─────────────────────────────────────────────────────────────────────────
def test_registry_entries_are_complete_and_sane() -> None:
    cities = REGISTRY["cities"]
    assert len(cities) >= 25  # AC-064 의 "조사 25개 이상"
    seen: set[str] = set()
    for city in cities:
        city_id = city["city_id"]
        assert city_id not in seen
        seen.add(city_id)
        assert city_id.replace("-", "").isalnum() and city_id.islower()
        assert city["name_ko"] and city["name_en"] and city["country_code"]
        assert -90 <= city["center"]["lat"] <= 90
        assert -180 <= city["center"]["lng"] <= 180
        assert city["center_note"]  # 중심점을 왜 거기로 잡았는지가 사람이 읽을 수 있게 남아야 한다
        assert 1000 <= city["radius_m"] <= 30000
        assert city["sitelink_min"] >= 1
        assert city["limit"] >= 1


def test_disabled_cities_keep_their_reason() -> None:
    """미달 도시를 지우지 않는다 — **왜 목록에 없는지가 정보다**(REQ-029).

    나트랑 5건 · 푸꾸옥 3건(한국어 라벨 0)은 반경 25km 로도 채워지지 않았다(실측 2026-09-15).
    지워 버리면 다음 사람이 같은 도시를 같은 방식으로 다시 재고 같은 결론에 도달한다.
    """
    disabled = [city for city in REGISTRY["cities"] if not city.get("enabled", True)]
    assert {city["city_id"] for city in disabled} == {"cebu", "nha-trang", "phu-quoc"}
    for city in disabled:
        assert city["disabled_reason"].strip()
        assert city["measured_2026_09_15"]["candidates"] >= 0


def test_measured_values_cite_their_source() -> None:
    """측정하지 않은 수치를 대장에 적지 않는다 — 적힌 것은 전부 출처가 있다."""
    measured = [city for city in REGISTRY["cities"] if "measured_2026_09_15" in city]
    assert len(measured) >= 30
    for city in measured:
        assert "_recon.md" in city["measured_2026_09_15"]["source"]


def test_sitelink_min_is_not_a_retry_knob() -> None:
    """하한은 **모든 도시에 같은 값**이다 — 도시별 재시도 손잡이가 아니다 (함정 F13).

    도쿄를 하한 32 로 재시도했을 때 32건이 10건이 됐다. 하한은 성능 손잡이가 아니라 선별
    기준이고, 그것을 흔들면 측정 대상이 측정 과정에 따라 달라진다.

    **값 자체는 한 번에 정해진다.** 이 테스트가 원래 도쿄의 15 를 못박고 있었는데, 그 뒤
    사람이 전 도시를 한 값으로 다시 정하면서(현재 대장) 값만으로는 규칙을 표현할 수 없게
    됐다 — 그래서 막는 것을 **값**에서 **분포**로 옮긴다. 한 도시만 조용히 올라가는 것이
    F13 이 말한 실패 양식이고, 그것은 값이 갈리는 순간 여기서 걸린다.
    """
    floors = {city["city_id"]: city["sitelink_min"] for city in REGISTRY["cities"]}
    assert len(set(floors.values())) == 1, f"도시마다 하한이 다르다 — 재시도로 흔든 흔적이다: {floors}"
    tokyo = next(city for city in REGISTRY["cities"] if city["city_id"] == "tokyo")
    assert tokyo["radius_m"] == 6000
    assert "F13" in " ".join(REGISTRY["rules"]) or "재시도" in " ".join(REGISTRY["rules"])


# ── 429 는 "그만 두드려"다 ────────────────────────────────────────────────────
class _Response:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.reason_phrase = "Too Many Requests" if status_code == 429 else "Server Error"
        self.headers = headers or {}
        self.request = None

    def raise_for_status(self) -> None:  # pragma: no cover - 4xx/5xx 는 위에서 막힌다
        raise AssertionError("여기에 오면 안 된다")

    def json(self) -> dict[str, object]:  # pragma: no cover
        return {}


def test_a_429_waits_far_longer_than_the_plain_backoff() -> None:
    """429 를 받으면 초 단위로 다시 두드리지 않는다.

    정찰 중 연속 질의로 WDQS 가 429 를 주기 시작하자, 몇 분 전 57.9초에 성공하던
    질의가 65.9초 504 로 바뀌었다. 고정 백오프로 계속 두드리면 조인 창이 길어진다.
    """
    from bakery.io import RETRY_BACKOFF_S, THROTTLED_BACKOFF_S

    assert THROTTLED_BACKOFF_S > max(RETRY_BACKOFF_S) * 2


def test_retry_after_is_read_in_seconds_and_ignored_when_unreadable() -> None:
    """서버가 지시한 대기를 따르되, 못 읽으면 조용히 기본값으로 돌아간다.

    HTTP-date 형식은 일부러 읽지 않는다 — 시계 차를 잘못 읽으면 **더 짧게** 기다리는
    쪽으로 틀린다.
    """
    from bakery.io import _retry_after_seconds

    assert _retry_after_seconds(_Response(429, {"Retry-After": "90"})) == 90.0
    assert _retry_after_seconds(_Response(429, {"Retry-After": " 30 "})) == 30.0
    assert _retry_after_seconds(_Response(429, {})) is None
    # HTTP-date · 음수 · 비정상적으로 긴 값은 읽지 않는다.
    assert _retry_after_seconds(_Response(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
    assert _retry_after_seconds(_Response(429, {"Retry-After": "-5"})) is None
    assert _retry_after_seconds(_Response(429, {"Retry-After": "99999"})) is None


# ── 콜로세움의 두 번째 문 ────────────────────────────────────────────────────
def test_excluding_stadiums_would_delete_the_colosseum() -> None:
    """경기장을 제외 목록에 넣으면 안 된다 — **평면으로도** 콜로세움이 지워진다.

    정찰에서는 `P279*` 로 '스포츠 시설'을 제외했다가 콜로세움을 잃었고, 그래서 제외를
    평면으로만 하기로 했다. 그런데 2026-09-16 굽기 결과를 보니 **콜로세움 자신이
    `Q483110`(경기장)을 `P31` 로 달고 있다** — 제외는 허용보다 먼저 적용되므로(규칙 1)
    평면 제외만으로도 지워진다. 같은 함정의 두 번째 문이다.

    경기장 잡음 18건은 감수한다. 거슬리는 도시는 항목 단위로 `city-denylist.json` 에 적는다.
    """
    from harbor_lantern.domain.guide_taxonomy import Taxonomy, classify

    # 2026-09-16 굽기의 rome.json 이 실제로 기록한 콜로세움의 분류 집합.
    colosseum = ["Q124830411", "Q112132548", "Q133444874", "Q7362268",
                 "Q839954", "Q112132522", "Q483110", "Q3867560"]

    allowed = {"Q839954": "monument"}
    kept = Taxonomy(exclude_flat=frozenset(), allow_flat=allowed, allow_root={})
    assert classify(colosseum, {}, kept).decision == "accept"

    # 경기장을 평면 제외에 넣는 순간 사라진다 — 이 단언이 실패하면 규칙 순서가 바뀐 것이다.
    banned = Taxonomy(exclude_flat=frozenset({"Q483110"}), allow_flat=allowed, allow_root={})
    assert classify(colosseum, {}, banned).decision == "exclude"

    # 그래서 실제 분류표에는 경기장이 제외로 들어가 있으면 안 된다.
    assert "Q483110" not in CLASSES["exclude_flat"], "경기장을 제외하면 콜로세움이 지워진다"
