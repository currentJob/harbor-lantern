"""FastAPI 의존성 — DB 연결 · 시계 · 설정 · 참가자 토큰 (설계서 §6.17 · DSN-03).

연결은 **요청마다 새로** 만들고 끝나면 닫는다. 연결을 앱 전역에 하나 두면 스레드풀에서
도는 동기 핸들러들이 같은 커서를 밟는다 — 그건 부하가 있을 때만 터진다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from harbor_lantern.clock import Clock
from harbor_lantern.config import Settings
from harbor_lantern.services import trip_service
from harbor_lantern.services.external.cache import CachedProvider
from harbor_lantern.storage import repo_trips
from harbor_lantern.storage.db import Database

__all__ = [
    "ClockDep",
    "Conn",
    "FxProviderDep",
    "SettingsDep",
    "TripAccess",
    "TripAccessDep",
    "WeatherProviderDep",
    "get_clock",
    "get_settings",
]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_clock(request: Request) -> Clock:
    return request.app.state.clock  # type: ignore[no-any-return]


def get_database(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def get_weather_provider(request: Request) -> CachedProvider:
    return request.app.state.weather_provider  # type: ignore[no-any-return]


def get_fx_provider(request: Request) -> CachedProvider:
    return request.app.state.fx_provider  # type: ignore[no-any-return]


def db_conn(request: Request) -> Iterator[sqlite3.Connection]:
    db: Database = request.app.state.db
    with db.connection() as conn:
        yield conn


SettingsDep = Annotated[Settings, Depends(get_settings)]
ClockDep = Annotated[Clock, Depends(get_clock)]
Conn = Annotated[sqlite3.Connection, Depends(db_conn)]
WeatherProviderDep = Annotated[CachedProvider, Depends(get_weather_provider)]
FxProviderDep = Annotated[CachedProvider, Depends(get_fx_provider)]

ParticipantToken = Annotated[
    str | None,
    Header(alias="X-Participant-Token", description="POST /api/trips 또는 POST /api/join 이 발급한 토큰"),
]


@dataclass(frozen=True)
class TripAccess:
    """여행 스코프 요청 하나의 컨텍스트. `trip` 행에는 그 시점의 `revision` 이 들어 있다."""

    trip: sqlite3.Row
    participant: sqlite3.Row
    conn: sqlite3.Connection


def trip_access(trip_id: str, conn: Conn, x_participant_token: ParticipantToken = None) -> TripAccess:
    """여행 + 참가자 확인. **없는 여행과 권한 없는 여행을 구분하지 않는다**(둘 다 404).

    403 은 "그 자원은 있다"를 알려 주는 신호이고, 무계정 공유 모델에서 그건 열거 창구다.
    """
    trip = repo_trips.get_trip(conn, trip_id)
    participant = trip_service.resolve_participant(conn, trip_id, x_participant_token)
    if trip is None:
        from harbor_lantern.api.errors import NotFoundError

        raise NotFoundError()
    return TripAccess(trip=trip, participant=participant, conn=conn)


TripAccessDep = Annotated[TripAccess, Depends(trip_access)]
