/* 근처 음식점·카페 목록 — REQ-017 · REQ-018 (DSN-27)
 *
 * 이 모듈은 **그리기만 한다.** 조회도, 일정 추가도 `main.js` 가 한다 —
 * 렌더 함수가 네트워크를 부르기 시작하면 테스트가 DOM 스텁만으로 돌지 않는다.
 */

import { escapeHtml, formatDistance } from '../format.js';

/** `stale` 이면 언제 받은 값인지 반드시 같이 말한다.
 *  낡은 값을 최신인 척 보여 주는 것이 가장 나쁘다(NFR-004 의 화면 쪽 귀결). */
function staleNote(result) {
  if (!result || !result.stale) return '';
  const when = result.fetched_at ? result.fetched_at.replace('T', ' ').replace('Z', ' UTC') : '알 수 없음';
  return `<div class="nearstale">지금 목록을 갱신하지 못했습니다 · 마지막 조회 ${escapeHtml(when)}</div>`;
}

/**
 * @param {HTMLElement} root  목록을 그릴 컨테이너
 * @param {object|null} result  `/api/nearby` 응답 (없으면 안내만)
 * @param {{busy?:boolean, message?:string}} ui
 */
export function renderNearby(root, result, ui = {}) {
  if (!root) return;

  if (ui.busy) {
    root.innerHTML = '<div class="nearnote">근처를 찾는 중…</div>';
    return;
  }
  if (ui.message) {
    root.innerHTML = `<div class="nearnote">${escapeHtml(ui.message)}</div>`;
    return;
  }
  if (!result) {
    root.innerHTML = '';
    return;
  }
  if (!result.available) {
    root.innerHTML = '<div class="nearnote">근처 정보를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.</div>';
    return;
  }
  if (!result.places.length) {
    root.innerHTML = `<div class="nearnote">반경 ${result.radius_m}m 안에 찾은 곳이 없습니다. 범위를 넓혀 보세요.</div>`;
    return;
  }

  const rows = result.places.map((place) => `
    <li class="nearitem" data-key="${escapeHtml(place.osm_type)}/${place.osm_id}">
      <div class="nearmain">
        <div class="nearname">${escapeHtml(place.name)}</div>
        <div class="nearmeta">${escapeHtml(place.category_label)} · ${escapeHtml(formatDistance(place.distance_m))}</div>
      </div>
      <a class="neargo" href="${escapeHtml(place.directions_url)}" target="_blank" rel="noopener">길찾기</a>
      <button class="nearadd" type="button" data-add="${escapeHtml(place.osm_type)}/${place.osm_id}">일정에</button>
    </li>`).join('');

  root.innerHTML = `${staleNote(result)}
    <div class="nearhead">반경 ${result.radius_m}m · ${result.places.length}곳</div>
    <ul class="nearlist">${rows}</ul>`;
}
