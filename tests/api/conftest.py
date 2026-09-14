"""API 테스트 공용 픽스처 — 여행 하나와 참가자 둘.

여기 있는 것은 **준비**일 뿐이고 단언은 하지 않는다. 픽스처 안에 숨은 assert 는
실패했을 때 어느 테스트의 책임인지 알 수 없게 만든다 — 다만 준비 자체가 실패하면
그 자리에서 멈춰야 하므로 상태코드만 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

__all__ = ["Actor", "TripFixture"]


@dataclass(frozen=True)
class Actor:
    """참가자 한 명 = 표시명 + 토큰. 무계정 모델이라 토큰이 유일한 신원이다(A10)."""

    participant_id: str
    display_name: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Participant-Token": self.token}


@dataclass(frozen=True)
class TripFixture:
    client: Any
    trip_id: str
    invite_code: str
    owner: Actor

    @property
    def base(self) -> str:
        return f"/api/trips/{self.trip_id}"

    @property
    def headers(self) -> dict[str, str]:
        return self.owner.headers

    def state(self, actor: Actor | None = None, *, headers: dict[str, str] | None = None) -> Any:
        """`headers` 를 주면 토큰 위에 덧씌운다(`If-None-Match` 폴링용)."""
        who = actor or self.owner
        return self.client.get(f"{self.base}/state", headers={**who.headers, **(headers or {})})

    def day(self, day_index: int, actor: Actor | None = None) -> dict[str, Any]:
        document = self.state(actor).json()
        return next(item for item in document["days"] if item["day_index"] == day_index)

    def spot_ids(self, day_index: int) -> list[str]:
        return [spot["id"] for spot in self.day(day_index)["spots"]]

    def revision(self) -> int:
        return int(self.state().json()["trip"]["revision"])

    def join(self, display_name: str) -> Actor:
        response = self.client.post(
            "/api/join",
            json={"invite_code": self.invite_code, "display_name": display_name},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        return Actor(body["participant"]["id"], display_name, body["participant_token"])


@pytest.fixture
def trip(client: Any) -> TripFixture:
    """27스팟 시드가 주입된 여행 1건 + 개설자 토큰."""
    response = client.post("/api/trips", json={"organizer_display_name": "개설자"})
    assert response.status_code == 201, response.text
    body = response.json()
    owner = Actor(body["participant"]["id"], "개설자", body["participant_token"])
    return TripFixture(client, body["trip"]["id"], body["trip"]["invite_code"], owner)


@pytest.fixture
def guest(trip: TripFixture) -> Actor:
    """초대코드로 들어온 두 번째 참가자 (AC-005 · AC-011 이 둘을 필요로 한다)."""
    return trip.join("동행")


@pytest.fixture
def fx_port(app: Any) -> Any:
    """환율 공급자를 **호출 횟수를 세는 가짜**로 바꾼다 (AC-026 · AC-029).

    기본 상태(진짜 어댑터)는 네트워크 차단에 걸려 `available:false` 가 되고, 그것도
    검증 대상이다 — 이 픽스처를 요청하지 않는 테스트가 그 경로를 본다.
    """
    from harbor_lantern.services.external.cache import CachedProvider
    from harbor_lantern.services.external.ports import FxSnapshot
    from tests.fakes import FX_SAMPLE, FakeFxPort

    port = FakeFxPort(FxSnapshot(**FX_SAMPLE))
    app.state.fx_provider = CachedProvider(
        port,
        "fx:HKD-KRW",
        app.state.settings.external.fx_ttl_s,
        app.state.db,
        app.state.clock,
    )
    return port


@pytest.fixture
def nearby_port(app: Any) -> Any:
    """근처 장소 공급자를 가짜로 바꾼다 (AC-054 · AC-055).

    `fx_port` 와 같은 이유로 둔다. 다만 캐시는 `CachedProvider` 가 아니라
    `NearbyProvider` 다 — 질의마다 키가 달라지기 때문이다(DSN-26).
    """
    from harbor_lantern.services.external.nearby import NearbyProvider
    from harbor_lantern.services.external.ports import PlaceSnapshot
    from tests.fakes import PLACES_SAMPLE, FakeNearbyPort

    port = FakeNearbyPort(tuple(PlaceSnapshot(**item) for item in PLACES_SAMPLE))
    app.state.nearby_provider = NearbyProvider(
        port, app.state.settings.nearby, app.state.db, app.state.clock,
    )
    return port
