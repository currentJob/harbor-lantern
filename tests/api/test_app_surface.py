"""앱 표면 — 헬스체크 · 정적 서빙 · CORS · 응답 헤더 · 여행 간 격리.

(NFR-006 · NFR-015 · DSN-23 · 설계서 §6.17)

`tests/test_api_integration.py` 가 보던 것을 여기로 옮겼다 — 브라우저가 실제로 부딪히는
표면(프리플라이트·정적 자산·헤더)은 API 테스트와 같은 곳에 있어야 한다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from harbor_lantern.api.app import create_app
from tests.api.conftest import TripFixture

PAGES_ORIGIN = "https://currentjob.github.io"


def test_health_reports_status_and_version(client: Any) -> None:
    """기동 확인용. 터널·CI·프론트가 이 한 줄을 본다."""
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_api_responses_are_not_cached_and_carry_hardening_headers(client: Any) -> None:
    """`/api/*` 는 `no-store` 다 — 공유 링크 모델에서 중간 캐시가 남의 일정을 들고 있으면 안 된다."""
    response = client.get("/api/health")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_process_time_header_is_present(client: Any) -> None:
    """DSN-23: 성능 하네스(`tools/perf_report.py`)가 읽는 값이다. 없으면 측정이 성립하지 않는다."""
    response = client.get("/api/health")
    assert float(response.headers["X-Process-Time"]) >= 0.0


def test_frontend_is_served_from_the_same_process(client: Any) -> None:
    """NFR-001 · NFR-015: 번들러도 CDN 도 없이 FastAPI 가 그대로 서빙한다."""
    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert client.get("/config.js").status_code == 200
    assert client.get("/vendor/leaflet-1.9.4/leaflet.js").status_code == 200
    assert client.get("/없는파일.js").status_code == 404


def test_cors_allows_the_configured_origin_only(settings: Any, fixed_clock: Any) -> None:
    """정적 호스팅(GitHub Pages)에서 이 API 를 부르는 경로만 연다.

    허용 목록이 비어 있지 않은데 아무 오리진이나 통과하면, 초대코드를 아는 사람이
    전체 편집 권한을 갖는 모델(A4)에서 CSRF 표면이 그대로 열린다.
    """
    app = create_app(settings=settings, clock=fixed_clock, allowed_origins=[PAGES_ORIGIN])
    with TestClient(app) as client:
        preflight_headers = {
            "Origin": PAGES_ORIGIN,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type,x-participant-token,if-none-match",
        }
        allowed = client.options("/api/trips/아무거나/spots/아무거나", headers=preflight_headers)
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == PAGES_ORIGIN

        rejected = client.options(
            "/api/trips",
            headers={**preflight_headers, "Origin": "https://untrusted.example"},
        )
        assert rejected.status_code == 400


def test_default_app_has_no_cross_origin_access(client: Any) -> None:
    """기본값은 **닫혀 있다** — 허용 오리진을 명시적으로 주기 전에는 아무도 못 부른다."""
    response = client.options(
        "/api/trips",
        headers={"Origin": "https://anywhere.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_token_of_another_trip_sees_nothing(trip: TripFixture, client: Any) -> None:
    """§6.17: 남의 여행 토큰으로는 조회도 편집도 안 된다. 응답은 403 이 아니라 **404** 다.

    403 은 "그 여행은 존재한다"를 알려 주는 신호이고, 무계정 공유 모델에서 그건 열거 창구다.
    """
    other = client.post("/api/trips", json={}).json()
    foreign = {"X-Participant-Token": other["participant_token"]}

    assert client.get(trip.base, headers=foreign).status_code == 404
    assert client.get(f"{trip.base}/state", headers=foreign).status_code == 404
    assert client.get(f"{trip.base}/expenses", headers=foreign).status_code == 404
    assert client.get(f"{trip.base}/settlement", headers=foreign).status_code == 404
    assert client.post(f"{trip.base}/days/1/optimize", headers=foreign).status_code == 404

    spot_id = trip.spot_ids(1)[0]
    assert client.put(f"{trip.base}/spots/{spot_id}/done", headers=foreign, json={"done": True}).status_code == 404
    assert trip.state().json()["progress"]["done"] == 0


def test_missing_and_garbage_tokens_are_404(trip: TripFixture, client: Any) -> None:
    """§6.17: 토큰 없음·엉터리 토큰도 같은 404 다(셋을 구분하면 그 차이가 정보다)."""
    assert client.get(trip.base).status_code == 404
    assert client.get(trip.base, headers={"X-Participant-Token": "not-a-real-token"}).status_code == 404


def test_create_app_is_callable_without_arguments() -> None:
    """설계서 §9: `uvicorn ... --factory` 가 인자 없이 부른다.

    시그니처가 바뀌면 원커맨드 실행(`python -m harbor_lantern`)이 그 자리에서 죽는다.
    """
    import inspect

    parameters = inspect.signature(create_app).parameters
    assert all(parameter.default is not inspect.Parameter.empty for parameter in parameters.values())
    assert {"settings", "clock"} <= set(parameters)
