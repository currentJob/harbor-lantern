/* 설계한 일정 → 지도 입력 어댑터 (REQ-024 · REQ-028 · AC-085 화면 확장)
 *
 * **지도를 새로 만들지 않는다.** 홍콩 화면이 쓰는 `js/map.js` 의 `TripMap` 을 그대로 쓴다.
 * 문제는 두 화면의 **데이터 모양이 다르다**는 것 하나뿐이다.
 *
 *   홍콩 `/state`      : days[].spots[]  — 스팟에 lat·lng·time_label·hours_text 가 있다.
 *   `/api/explore/plan`: days[].stops[].place — 시각은 stop 에, 좌표·이름은 place 에 있다.
 *
 * 그래서 `TripMap.render()` 의 시그니처를 건드리는 대신 **여기서 모양만 바꾼다**. 지도 쪽을
 * 고쳤으면 홍콩 화면이 같이 움직였을 것이고, 그 회귀는 이 화면의 테스트에 보이지 않는다.
 *
 * DOM 을 만지지 않는 **순수 함수**만 둔다 — `tests/static/test_plan_map.py` 가 Node 로
 * 이 파일을 그대로 돌린다(`render/guide.js` 와 같은 방식).
 */

import { safeColor, stopDomId } from './guide.js';

/** 일자 색. 서버 `config.py` 의 `day_palette` 와 같은 값이다.
 *  구운 가이드 일정은 색을 실어 오지만 **폴백 일정에는 색이 없다** — 그때 여기서 고른다. */
export const DAY_COLORS = ['#22d3ee', '#f472b6', '#fbbf24', '#a78bfa'];

const text = (value) => String(value == null ? '' : value).trim();

/** 좌표 한 개. 숫자가 아니면 `null` — 없는 값을 0 으로 읽으면 서아프리카 앞바다에 핀이 선다. */
function coordinate(value) {
  const number = Number(value);
  return text(value) !== '' && Number.isFinite(number) ? number : null;
}

/** 일자 색 — 데이터의 색이 먼저고, 없거나 이상하면 팔레트에서 고른다. */
export function dayColor(day, index) {
  return safeColor(day && day.color) || DAY_COLORS[(Number(index) || 0) % DAY_COLORS.length];
}

/** 팝업 둘째 줄에 쓰이는 일자 이름. 테마가 없는 폴백 일정은 `DAY n` 이 된다. */
export function dayTitle(day, index) {
  const source = day || {};
  return [text(source.title), text(source.area)].filter(Boolean).join(' · ')
    || `DAY ${(Number(index) || 0) + 1}`;
}

/** stop 하나 → `TripMap` 이 아는 spot 하나.
 *
 *  좌표가 없으면 `null` 이다 — 지도에 올릴 방법이 없다(목록에는 그대로 남는다).
 *  영업시간 필드 이름이 두 경로에서 다르다: 구운 가이드는 `hours_text`, 폴백은 `opening_hours`.
 */
export function spotFromStop(stop, dayIndex, stopIndex) {
  const source = stop || {};
  const place = source.place || {};
  const lat = coordinate(place.lat);
  const lng = coordinate(place.lng);
  if (lat === null || lng === null) return null;
  const span = [text(source.arrival), text(source.departure)].filter(Boolean).join('–');
  return {
    id: stopDomId(dayIndex, stopIndex),
    lat,
    lng,
    name: text(place.name) || text(place.name_ko) || '이름 미제공',
    name_original: text(place.name_original),
    time_label: span || '시각 미정',
    hours_text: text(place.hours_text) || text(place.opening_hours),
  };
}

/** 일정 응답 전체 → `TripMap.render(days)` 가 기대하는 배열.
 *
 *  `id` 는 목록 카드의 DOM id 와 **같은 문자열**이다(`stopDomId`). 그래서 핀을 누르면
 *  목록의 그 자리로 갈 수 있고, 목록에만 있고 지도에 없는 항목이 생기면 테스트가 잡는다.
 */
export function planMapDays(plan) {
  return ((plan && plan.days) || []).map((day, index) => ({
    day_index: index,
    title: dayTitle(day, index),
    color: dayColor(day, index),
    spots: (((day || {}).stops) || [])
      .map((stop, order) => spotFromStop(stop, index, order))
      .filter(Boolean),
  }));
}

/** 지도에 실제로 선 핀의 수. */
export function planMapSpotCount(days) {
  return (days || []).reduce((total, day) => total + ((day && day.spots) || []).length, 0);
}

/** 좌표가 없어 지도에 못 올린 스팟 수. 0 이 아니면 화면이 그 사실을 말해야 한다 —
 *  말하지 않으면 사용자는 "목록에는 있는데 핀이 없다"를 지도 고장으로 읽는다. */
export function unmappedCount(plan) {
  const stops = ((plan && plan.days) || [])
    .reduce((total, day) => total + (((day || {}).stops) || []).length, 0);
  return stops - planMapSpotCount(planMapDays(plan));
}

/** 일자 선택 상자에 쓰는 한 줄. 핀이 없는 날은 고를 수 없어야 하므로 개수를 같이 적는다. */
export function dayPickerLabel(day) {
  const source = day || {};
  return `DAY ${(Number(source.day_index) || 0) + 1} · ${source.title} · ${((source.spots) || []).length}곳`;
}
