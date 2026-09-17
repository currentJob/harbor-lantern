"""All-day audit and review-aware proposals, separate from revision-cached /state."""

from harbor_lantern.domain.hours import is_open_at, parse_hours
from harbor_lantern.domain.review_plan import legs_for, propose_order, review_bonus, review_score
from harbor_lantern.domain.util import parse_hhmm
from harbor_lantern.services import plan_service
from harbor_lantern.services.external.reviews import collect_reviews, review_summary
from harbor_lantern.storage import repo_spots, repo_trips


def build_review(conn, trip, settings, provider, use_reviews=False):
    trip_id = str(trip["id"])
    conn.execute("BEGIN")
    try:
        trip = repo_trips.get_trip(conn, trip_id)
        rows = repo_spots.list_spots_by_trip(conn, trip_id)
        visits = repo_spots.list_visits(conn, trip_id)
        names = {str(p["id"]): str(p["display_name"]) for p in repo_trips.list_participants(conn, trip_id)}
        days = list(repo_trips.list_days(conn, trip_id))
    finally:
        conn.rollback()  # End the read snapshot before any provider I/O.
    by_id = {str(row["id"]): row for row in rows}
    # Read a single snapshot before network lookup. The captured revision protects apply.
    evidence = collect_reviews(provider, [dict(row) for row in rows], use_reviews)
    result = []
    for day in days:
        index = int(day["day_index"])
        day_rows = [r for r in rows if int(r["day_index"]) == index]

        def evaluate(order, day=day, index=index):
            state, warnings, conflicts = plan_service._build_day(
                day, index, [by_id[s["id"]] for s in order], visits, names, settings.travel)
            for spot in state["spots"]:
                schedule = spot["schedule"]
                departure = parse_hhmm(schedule["depart_local"])
                weekday = (state["weekday"] + schedule["depart_day_offset"]) % 7
                if is_open_at(parse_hours(spot["hours_text"]), departure, weekday) == "closed":
                    warnings.append({"spot_id": spot["id"], "kind": "closed_before_departure",
                                     "message": "예상 체류 종료가 영업시간 밖입니다. 체류시간을 조정하세요."})
            problems = {(w["spot_id"], w["kind"]) for w in warnings}
            problems.update((c["spot_id"], "conflict:" + c["fixed_spot_id"]) for c in conflicts)
            cost = state["totals"]["travel_minutes"]
            cost += sum(c["overlap_minutes"] for c in conflicts)
            for spot in state["spots"]:
                schedule = spot["schedule"]
                eta = parse_hhmm(schedule["eta_local"]) + schedule["eta_day_offset"] * 1440
                cost += review_bonus(evidence.get(spot["id"])) * (eta - parse_hhmm(day["start_local"])) / 60
            return problems, cost, {"day": state, "warnings": warnings, "conflicts": conflicts}

        initial, _, _ = plan_service._build_day(day, index, day_rows, visits, names, settings.travel)
        order, current, proposed = propose_order(initial["spots"], evaluate)
        proposed_day = proposed[2]["day"]
        for spot in proposed_day["spots"]:
            spot["review"] = evidence[spot["id"]]
            score = review_score(spot["review"])
            spot["review_priority"] = round(score, 2) if score is not None else None
        unknown = sum(s["hours"]["status"] == "unknown" for s in initial["spots"])
        duration = sum(initial["totals"][key] for key in ("travel_minutes", "dwell_minutes"))
        advice = []
        if unknown:
            advice.append(f"{unknown}곳의 영업시간을 해석하지 못했습니다. 방문 전 원문을 확인하세요.")
        if duration > 600:
            advice.append(f"이동·관람 합계가 {duration // 60}시간 {duration % 60}분입니다. "
                          "체류시간이나 방문 수를 줄여 보세요.")
        if not day_rows:
            advice.append("일정이 비어 있습니다. 장소를 추가해 주세요.")
        if len(day_rows) > 40:
            advice.append("하루 40곳을 초과해 자동 재정렬은 생략했습니다. 일정 점검과 길찾기는 제공됩니다.")
        for spot in proposed_day["spots"]:
            if spot["fixed_start_local"]:
                eta = parse_hhmm(spot["schedule"]["eta_local"]) + spot["schedule"]["eta_day_offset"] * 1440
                wait = parse_hhmm(spot["fixed_start_local"]) - eta
                if wait > 0:
                    advice.append(f"{spot['name']}: 고정시각 {spot['fixed_start_local']}까지 "
                                  f"약 {wait}분 여유가 있습니다. "
                                  "대기시간을 포함하도록 앞 장소의 체류시간을 조정하세요.")
            if spot["review"].get("business_status") in {"CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY"}:
                advice.append(f"{spot['name']}: Google Maps에 휴업·폐업으로 표시됩니다. 방문 여부를 확인하세요.")
        changed = [s["id"] for s in order] != [s["id"] for s in initial["spots"]]
        result.append({"day_index": index, "date": str(day["date"]), "title": str(day["title"]),
                       "current": current[2], "proposed": proposed[2], "improved": changed,
                       "proposed_order": [s["id"] for s in order], "advice": advice,
                       "routes": legs_for(proposed_day["spots"]),
                       "reason": ("영업시간 경고·고정시각 충돌을 늘리지 않는 범위에서 "
                                  "이동시간과 리뷰 우선순위를 함께 고려한 제안입니다."
                                  if changed else "현재 제약 안에서 더 나은 순서를 찾지 못했습니다. "
                                  "현재 일정을 유지합니다.")})
    return {"expected_revision": int(trip["revision"]), "days": result,
            "review_summary": review_summary(evidence), "reviews_enabled": provider.reviews_enabled,
            "notice": ("모든 날짜를 점검했습니다. 첫·마지막 장소, 완료한 구간, 고정시각과 시간대 순서를 유지합니다. "
                       "이동은 직선거리 기반 예상이며 실제 도로·환승·페리 경로는 구간 길찾기에서 확인하세요.")}
