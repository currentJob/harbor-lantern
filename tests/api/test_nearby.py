"""근처 음식점·카페 조회 — REQ-017 · REQ-018 (AC-050~AC-057).

이 파일이 지키는 것은 셋이다.

1. **경계값이 422 로 막히는가** (AC-052 · AC-053). 조용히 잘라 내면 사용자는 자기가
   무엇을 잘못 보냈는지 모른 채 엉뚱한 결과를 본다.
2. **캐시가 정말 외부를 안 때리는가** (AC-054). 이것은 반환값으로 증명되지 않는다 —
   가짜 포트의 `.calls` 카운터로만 증명된다.
3. **공급자가 죽어도 앱이 안 죽는가** (AC-055). "근처 맛집" 버튼 하나가 500 을 내면
   안 된다.
"""

from __future__ import annotations

from typing import Any

import pytest

TST_LAT, TST_LNG = 22.2937, 114.1730  # 스타 애비뉴 부근


def get_nearby(client: Any, **params: Any) -> Any:
    query = {"lat": TST_LAT, "lng": TST_LNG, **params}
    return client.get("/api/nearby", params=query)


# ── AC-050 : 목록과 필수 필드 ─────────────────────────────────────────────
def test_returns_places_with_required_fields(client: Any, nearby_port: Any) -> None:
    response = get_nearby(client)
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["places"], "표본이 세 건인데 결과가 비었다"
    for place in body["places"]:
        for field in ("name", "lat", "lng", "category", "distance_m", "directions_url"):
            assert field in place, f"{field} 가 응답에 없다 (AC-050)"
        assert place["directions_url"].startswith("https://www.google.com/maps/dir/")


def test_echoes_the_query_it_answered(client: Any, nearby_port: Any) -> None:
    """어떤 좌표·반경에 대한 답인지 응답이 스스로 말해야 한다.

    캐시를 공유하는 구조라, 응답만 보고 "무엇에 대한 답인지" 알 수 없으면
    화면이 낡은 결과를 새 위치의 것으로 그린다.
    """
    body = get_nearby(client, radius_m=1200).json()
    assert body["origin_lat"] == pytest.approx(TST_LAT)
    assert body["origin_lng"] == pytest.approx(TST_LNG)
    assert body["radius_m"] == 1200
    assert body["source"] == "openstreetmap-overpass"


# ── AC-051 : 거리 오름차순 ────────────────────────────────────────────────
def test_places_are_sorted_by_distance(client: Any, nearby_port: Any) -> None:
    places = get_nearby(client).json()["places"]
    distances = [place["distance_m"] for place in places]
    assert distances == sorted(distances), f"거리순이 아니다: {distances}"


def test_distance_is_measured_from_the_caller_not_the_cache_key(
    client: Any, nearby_port: Any,
) -> None:
    """캐시 키는 좌표를 양자화하지만 **거리는 실제 좌표로** 재야 한다.

    거리를 캐시에 같이 넣으면 최대 110m 어긋난 값이 굳는다. 양자화 자리 안에서
    좌표를 조금 옮기면(=같은 캐시 항목) 거리가 그만큼 달라져야 정상이다.
    """
    near = get_nearby(client).json()["places"][0]["distance_m"]
    far = get_nearby(client, lat=TST_LAT + 0.0004).json()["places"][0]["distance_m"]
    assert near != pytest.approx(far), "좌표를 옮겼는데 거리가 그대로다 — 거리가 캐시에 굳었다"


# ── AC-052 · AC-053 : 경계값 거절 ─────────────────────────────────────────
@pytest.mark.parametrize("radius", [3001, 10_000, 99])
def test_radius_outside_the_contract_is_rejected(
    client: Any, nearby_port: Any, radius: int,
) -> None:
    assert get_nearby(client, radius_m=radius).status_code == 422


def test_default_radius_is_800m(client: Any, nearby_port: Any) -> None:
    assert get_nearby(client).json()["radius_m"] == 800


@pytest.mark.parametrize(("lat", "lng"), [(91, 114.17), (-91, 114.17),
                                          (22.29, 181), (22.29, -181)])
def test_coordinates_outside_the_globe_are_rejected(
    client: Any, nearby_port: Any, lat: float, lng: float,
) -> None:
    assert client.get("/api/nearby", params={"lat": lat, "lng": lng}).status_code == 422


def test_missing_coordinates_are_rejected(client: Any, nearby_port: Any) -> None:
    assert client.get("/api/nearby").status_code == 422
    assert client.get("/api/nearby", params={"lat": TST_LAT}).status_code == 422


def test_a_disabled_category_is_rejected_not_ignored(client: Any, nearby_port: Any) -> None:
    """꺼진 카테고리를 조용히 무시하면 "결과 없음"과 구분되지 않는다.

    `attraction` 은 설정에 존재하지만 `enabled=False` 다 — 확장 지점이 살아 있다는
    증거이자, 지금은 노출되지 않는다는 계약이다.
    """
    response = get_nearby(client, category=["attraction"])
    assert response.status_code == 422
    assert nearby_port.calls == 0, "거절할 요청으로 외부를 때렸다"


def test_enabled_categories_are_accepted(client: Any, nearby_port: Any) -> None:
    response = get_nearby(client, category=["restaurant", "cafe"])
    assert response.status_code == 200
    assert response.json()["categories"] == ["restaurant", "cafe"]
    assert nearby_port.last_call["categories"] == ("restaurant", "cafe")


# ── AC-054 : 캐시가 외부 호출을 막는다 ────────────────────────────────────
def test_second_identical_request_does_not_hit_the_provider(
    client: Any, nearby_port: Any,
) -> None:
    get_nearby(client)
    assert nearby_port.calls == 1
    get_nearby(client)
    assert nearby_port.calls == 1, "TTL 안인데 외부를 다시 때렸다 (AC-054)"


def test_a_small_step_still_hits_the_same_cache_entry(
    client: Any, nearby_port: Any,
) -> None:
    """걸어 다니면 좌표가 조금씩 바뀐다 — 그때마다 질의하면 공용 서버가 429 를 준다.

    양자화 자리(소수 3자리 ≈ 110m) 안의 이동은 같은 캐시를 맞혀야 한다.
    """
    get_nearby(client)
    get_nearby(client, lat=TST_LAT + 0.0002, lng=TST_LNG + 0.0002)
    assert nearby_port.calls == 1, "110m 안에서 움직였는데 캐시를 못 맞혔다"


def test_a_real_move_is_a_different_query(client: Any, nearby_port: Any) -> None:
    get_nearby(client)
    get_nearby(client, lat=TST_LAT + 0.05)  # 약 5km
    assert nearby_port.calls == 2, "다른 동네인데 같은 캐시를 줬다"


def test_radius_is_part_of_the_cache_key(client: Any, nearby_port: Any) -> None:
    get_nearby(client, radius_m=800)
    get_nearby(client, radius_m=2000)
    assert nearby_port.calls == 2


# ── AC-055 : 실패해도 5xx 를 내지 않는다 ──────────────────────────────────
def test_provider_failure_without_cache_is_unavailable_not_500(
    client: Any, nearby_port: Any,
) -> None:
    nearby_port.error = RuntimeError("overpass 가 죽었다")
    response = get_nearby(client)
    assert response.status_code == 200, "위젯 하나가 앱 전체를 죽였다"
    body = response.json()
    assert body["available"] is False
    assert body["places"] == []


def test_provider_failure_after_a_success_serves_stale(
    client: Any, nearby_port: Any,
) -> None:
    """낡은 값을 주는 것은 괜찮다. **낡았다고 말하지 않는 것**이 문제다."""
    first = get_nearby(client).json()
    assert first["stale"] is False

    nearby_port.error = RuntimeError("overpass 가 죽었다")
    # TTL 을 넘겨 갱신을 시도하게 만든다.
    client.app.state.clock.advance(client.app.state.settings.nearby.ttl_s + 60)

    body = get_nearby(client).json()
    assert body["available"] is True
    assert body["stale"] is True, "낡은 값을 최신인 척 돌려줬다"
    assert body["fetched_at"] == first["fetched_at"], "stale 인데 조회 시각이 갱신됐다"
    assert body["places"], "마지막 성공값을 잃었다"


def test_the_provider_is_never_called_for_a_rejected_request(
    client: Any, nearby_port: Any,
) -> None:
    get_nearby(client, radius_m=99_999)
    get_nearby(client, lat=999)
    assert nearby_port.calls == 0


# ── AC-056 : 어댑터가 거른 결과가 응답까지 이어지는가 ─────────────────────
def test_every_returned_place_has_a_usable_name(client: Any, nearby_port: Any) -> None:
    """이름 없는 항목을 거르는 것은 도메인의 일이지만(`domain/places.py`),
    그 결과가 **응답까지 이어지는지**는 여기서 본다. 두 곳이 갈라지면 도메인 단위
    테스트만 초록색인 채 사용자는 이름 없는 카드를 본다.
    """
    for place in get_nearby(client).json()["places"]:
        assert place["name"].strip(), "이름이 빈 장소가 응답에 실렸다 (AC-056)"


def test_categories_are_labelled_for_display(client: Any, nearby_port: Any) -> None:
    """화면이 `restaurant` 를 그대로 그리면 안 된다 — 라벨은 설정 표가 준다."""
    labels = {place["category_label"] for place in get_nearby(client).json()["places"]}
    assert labels <= {"음식점", "카페", "간편식"}, labels


# ── AC-057 : 찾은 장소를 일정에 추가 ──────────────────────────────────────
def test_a_found_place_can_be_added_to_the_itinerary(
    client: Any, nearby_port: Any, trip: Any,
) -> None:
    """조회 결과를 그대로 스팟으로 넣으면 일반 스팟과 똑같이 동작해야 한다 (REQ-018).

    "추가된다"로 끝나면 안 된다 — **타임라인·진행률 집계에 반영**되는 것까지가 요구사항이다.
    새 스팟은 별도 경로가 아니라 기존 스팟 CRUD 를 그대로 탄다.
    """
    place = get_nearby(client).json()["places"][0]

    before = client.get(f"/api/trips/{trip.trip_id}/state", headers=trip.headers).json()
    before_total = before["progress"]["total"]

    created = client.post(
        f"/api/trips/{trip.trip_id}/days/1/spots",
        json={
            "name": place["name"],
            "lat": place["lat"],
            "lng": place["lng"],
            "time_label": "점심",
            "tip": f"근처 검색으로 추가 ({place['category_label']})",
        },
        headers=trip.headers,
    )
    assert created.status_code == 201, created.text

    after = client.get(f"/api/trips/{trip.trip_id}/state", headers=trip.headers).json()
    assert after["progress"]["total"] == before_total + 1

    day1 = next(day for day in after["days"] if day["day_index"] == 1)
    added = next(spot for spot in day1["spots"] if spot["name"] == place["name"])
    assert added["schedule"]["eta_local"], "도착 예상시각이 계산되지 않았다"
    assert day1["totals"]["distance_m"] > 0
