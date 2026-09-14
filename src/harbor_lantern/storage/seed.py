"""시드 로드 · 검증 · 주입 — DSN-06 (설계서 §5.3 · REQ-001 · AC-001).

시드는 코드가 아니라 **데이터 파일**(`seed/spots.json`)이고, 형태는
`contracts/seed-spots.schema.json` 이 정의한다. 검증은 **수기**로 한다 —
`jsonschema` 를 의존성에 넣으면 런타임 의존성이 하나 늘고(NFR-002 의 락파일·SBOM·
스캔 비용) 정작 확인하고 싶은 것은 계약이 못박은 다섯 가지뿐이다:

1. 일자 4개 · 총 27스팟 · 일자별 6·9·8·4 (전사 실수를 로드 시점에 터뜨린다)
2. 필수 필드 존재
3. 색상 `^#[0-9a-f]{6}$`
4. 좌표 범위
5. `schema_version` · `source` 고정값

**여기서 실패하면 여행 생성이 통째로 실패해야 한다.** 27개 중 하나가 조용히 빠진
여행을 만들어 놓으면 그 뒤의 모든 계산이 조용히 틀린다.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from harbor_lantern.domain.util import add_days, is_hhmm
from harbor_lantern.storage import repo_trips
from harbor_lantern.storage.repo_spots import insert_spot

__all__ = [
    "EXPECTED_DAY_SPOT_COUNTS",
    "EXPECTED_TOTAL_SPOTS",
    "SEED_PATH",
    "SeedError",
    "install_seed",
    "load_seed",
    "validate_seed",
]

# `src/harbor_lantern/storage/seed.py` → parents[3] 이 프로젝트 루트다.
SEED_PATH = Path(__file__).resolve().parents[3] / "seed" / "spots.json"

SCHEMA_VERSION = "1.0"
SOURCE = "reference/original-static-page.html"
EXPECTED_DAY_SPOT_COUNTS: tuple[int, ...] = (6, 9, 8, 4)
EXPECTED_TOTAL_SPOTS = sum(EXPECTED_DAY_SPOT_COUNTS)  # 27 (AC-001)

_COLOR = re.compile(r"^#[0-9a-f]{6}$")
_START_LOCAL = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")

_DAY_FIELDS = ("day_index", "label", "title", "area", "color", "start_local", "spots")
_SPOT_FIELDS = (
    "time_label",
    "name",
    "name_original",
    "tip",
    "hours_text",
    "closed_text",
    "description",
    "recommendation",
    "lat",
    "lng",
)


class SeedError(ValueError):
    """시드 파일이 계약을 어겼다. 메시지에 **어디가** 틀렸는지 담는다."""


def load_seed(path: Path | None = None) -> dict[str, Any]:
    """파일을 읽고 검증해서 돌려준다. 검증을 건너뛰는 경로는 만들지 않는다."""
    source = SEED_PATH if path is None else Path(path)
    document = json.loads(source.read_text(encoding="utf-8"))
    validate_seed(document)
    return document


def validate_seed(document: Any) -> None:
    """`contracts/seed-spots.schema.json` 이 못박은 것을 수기로 검사한다."""
    if not isinstance(document, dict):
        raise SeedError("시드 최상위는 객체여야 한다")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise SeedError(f"schema_version 이 {SCHEMA_VERSION!r} 이 아니다: {document.get('schema_version')!r}")
    if document.get("source") != SOURCE:
        raise SeedError(f"source 가 {SOURCE!r} 이 아니다: {document.get('source')!r}")

    days = document.get("days")
    if not isinstance(days, list) or len(days) != len(EXPECTED_DAY_SPOT_COUNTS):
        actual = len(days) if isinstance(days, list) else days
        raise SeedError(f"days 는 {len(EXPECTED_DAY_SPOT_COUNTS)}개여야 한다: {actual!r}")

    total = 0
    for ordinal, day in enumerate(days, start=1):
        _validate_day(day, ordinal)
        total += len(day["spots"])

    if total != EXPECTED_TOTAL_SPOTS:
        raise SeedError(f"총 스팟 수가 {EXPECTED_TOTAL_SPOTS}개가 아니다: {total}")


def _validate_day(day: Any, ordinal: int) -> None:
    where = f"days[{ordinal - 1}]"
    if not isinstance(day, dict):
        raise SeedError(f"{where} 는 객체여야 한다")
    missing = [field for field in _DAY_FIELDS if field not in day]
    if missing:
        raise SeedError(f"{where} 에 필수 필드가 없다: {missing}")
    if day["day_index"] != ordinal:
        raise SeedError(f"{where}.day_index 는 {ordinal} 이어야 한다: {day['day_index']!r}")
    if not _COLOR.match(str(day["color"])):
        raise SeedError(f"{where}.color 가 '#rrggbb'(소문자) 형태가 아니다: {day['color']!r}")
    if not _START_LOCAL.match(str(day["start_local"])):
        raise SeedError(f"{where}.start_local 이 'HH:MM' 이 아니다: {day['start_local']!r}")
    for field in ("label", "title", "area"):
        if not isinstance(day[field], str) or not day[field].strip():
            raise SeedError(f"{where}.{field} 가 비어 있다")

    spots = day["spots"]
    expected = EXPECTED_DAY_SPOT_COUNTS[ordinal - 1]
    if not isinstance(spots, list) or len(spots) != expected:
        raise SeedError(f"{where}.spots 는 {expected}개여야 한다: {len(spots) if isinstance(spots, list) else spots!r}")
    for index, spot in enumerate(spots):
        _validate_spot(spot, f"{where}.spots[{index}]")


def _validate_spot(spot: Any, where: str) -> None:
    if not isinstance(spot, dict):
        raise SeedError(f"{where} 는 객체여야 한다")
    missing = [field for field in _SPOT_FIELDS if field not in spot]
    if missing:
        raise SeedError(f"{where} 에 필수 필드가 없다: {missing}")
    if not isinstance(spot["name"], str) or not spot["name"].strip():
        raise SeedError(f"{where}.name 이 비어 있다")
    if not isinstance(spot["time_label"], str) or not spot["time_label"].strip():
        raise SeedError(f"{where}.time_label 이 비어 있다")
    lat, lng = spot["lat"], spot["lng"]
    if not isinstance(lat, int | float) or isinstance(lat, bool) or not -90.0 <= float(lat) <= 90.0:
        raise SeedError(f"{where}.lat 이 [-90, 90] 밖이다: {lat!r}")
    if not isinstance(lng, int | float) or isinstance(lng, bool) or not -180.0 <= float(lng) <= 180.0:
        raise SeedError(f"{where}.lng 가 [-180, 180] 밖이다: {lng!r}")
    dwell = spot.get("dwell_minutes")
    if dwell is not None and (not isinstance(dwell, int) or isinstance(dwell, bool) or dwell < 0):
        raise SeedError(f"{where}.dwell_minutes 는 null 이거나 0 이상 정수여야 한다: {dwell!r}")


def install_seed(
    conn: sqlite3.Connection,
    *,
    trip_id: str,
    start_date: str,
    created_at: str,
    document: Mapping[str, Any] | None = None,
) -> None:
    """4개 일자와 27개 스팟을 주입한다 (REQ-001 · AC-001 · AC-002).

    호출부가 트랜잭션을 연다. 날짜는 `start_date` 부터 **연속 배정**하고, 일자의
    `start_local` 은 시드에 굳어 있는 값을 그대로 쓴다 — 매번 첫 스팟에서 유도하면
    재정렬로 첫 스팟이 바뀔 때 하루 전체가 밀린다(§5.3).
    """
    seed = load_seed() if document is None else document
    for day in seed["days"]:
        day_index = int(day["day_index"])
        day_id = uuid.uuid4().hex
        repo_trips.insert_day(
            conn,
            day_id=day_id,
            trip_id=trip_id,
            day_index=day_index,
            date=add_days(start_date, day_index - 1),
            title=str(day["title"]),
            area=str(day["area"]),
            color=str(day["color"]),
            start_local=str(day["start_local"]),
        )
        _install_day_spots(conn, trip_id=trip_id, day_id=day_id, spots=day["spots"], created_at=created_at)


def _install_day_spots(
    conn: sqlite3.Connection,
    *,
    trip_id: str,
    day_id: str,
    spots: Sequence[Mapping[str, Any]],
    created_at: str,
) -> None:
    for position, spot in enumerate(spots):
        time_label = str(spot["time_label"])
        insert_spot(
            conn,
            spot_id=uuid.uuid4().hex,
            trip_id=trip_id,
            day_id=day_id,
            position=position,
            time_label=time_label,
            # `HH:MM` 형태의 시간대 라벨은 고정시각 일정이다 — 앵커가 여기서 유도된다(§5.3).
            fixed_start_local=time_label if is_hhmm(time_label) else None,
            name=str(spot["name"]),
            name_original=str(spot["name_original"]),
            tip=str(spot["tip"]),
            hours_text=str(spot["hours_text"]),
            closed_text=str(spot["closed_text"]),
            description=str(spot["description"]),
            recommendation=str(spot["recommendation"]),
            lat=float(spot["lat"]),
            lng=float(spot["lng"]),
            dwell_minutes=spot.get("dwell_minutes"),
            created_at=created_at,
            updated_at=created_at,
        )
