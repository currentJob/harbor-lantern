/* 미쉐린 홍콩·마카오 목록 — REQ-019 (DSN-28)
 *
 * `nearby` 와 달리 **좌표가 없는 항목이 정상**이다. 그런 곳은 거리를 숨기고
 * 검색 링크만 준다 — 목록에서 빼면 사용자는 그만큼만 존재한다고 믿는다.
 *
 * 리뷰 본문을 우리가 들고 있지 않다는 것도 화면이 숨기지 않는다. 무료로 가져올
 * 경로가 없어서 **링크로 넘긴다**: 홍콩에서 현지인이 실제로 보는 곳은 OpenRice 다.
 */

import { escapeHtml, formatDistance } from '../format.js';

const STAR = { 3: '★★★', 2: '★★', 1: '★' };

/** 이름으로 검색 링크를 만든다. 우리가 좌표를 몰라도 사용자는 찾아갈 수 있다. */
function searchLinks(place) {
  const q = encodeURIComponent(`${place.name} ${place.city_label}`);
  // OpenRice 는 도시별로 경로가 다르다 — 마카오 가게를 홍콩 경로로 검색하면
  // 결과가 0건인데 "그런 가게가 없다"로 읽힌다.
  const orCity = place.city === 'MO' ? 'macau' : 'hongkong';
  const orQuery = encodeURIComponent(place.name);
  return `
    <a class="curatedgo" href="https://www.google.com/maps/search/?api=1&query=${q}"
       target="_blank" rel="noopener">지도</a>
    <a class="curatedgo" href="https://www.openrice.com/en/${orCity}/restaurants?what=${orQuery}"
       target="_blank" rel="noopener">리뷰</a>`;
}

function row(place) {
  const distance = place.distance_m != null
    ? `<span class="curateddist">${escapeHtml(formatDistance(place.distance_m))}</span>`
    : '<span class="curatednoloc" title="위치를 확인하지 못해 거리 계산에서 빠집니다">위치 미확인</span>';
  const where = place.district || place.address || place.city_label;
  return `
    <li class="curateditem">
      <div class="curatedmain">
        <div class="curatedname"><b class="curatedstar">${STAR[place.stars] || ''}</b> ${escapeHtml(place.name)}</div>
        <div class="curatedmeta">${escapeHtml(where)} · ${distance}</div>
      </div>
      ${searchLinks(place)}
    </li>`;
}

/**
 * @param {HTMLElement} root
 * @param {object|null} data  `/api/curated` 응답
 * @param {{busy?:boolean, message?:string, limit?:number}} ui
 */
export function renderCurated(root, data, ui = {}) {
  if (!root) return;
  if (ui.busy) { root.innerHTML = '<div class="nearnote">목록을 불러오는 중…</div>'; return; }
  if (ui.message) { root.innerHTML = `<div class="nearnote">${escapeHtml(ui.message)}</div>`; return; }
  if (!data) { root.innerHTML = ''; return; }

  const limit = ui.limit || 20;
  const shown = data.places.slice(0, limit);
  const located = data.places.filter((p) => p.lat != null).length;

  // 한계를 접어서라도 **반드시 화면에 둔다.** 출처와 빠진 것을 모르면 이 목록을
  // "홍콩 맛집 전부"로 읽게 된다.
  const gaps = (data.known_gaps || []).map((g) => `<li>${escapeHtml(g)}</li>`).join('');

  root.innerHTML = `
    <div class="curatedhead">
      <b>미쉐린 홍콩·마카오</b> ${data.counts.total}곳
      <span class="curatedsub">· 위치 확인 ${located}곳 · 조사 ${escapeHtml(data.retrieved_at)}</span>
    </div>
    <ul class="curatedlist">${shown.map(row).join('')}</ul>
    ${data.places.length > limit
      ? `<div class="curatedmore">그 외 ${data.places.length - limit}곳</div>` : ''}
    <details class="curatedgaps">
      <summary>이 목록에 대해 · 출처와 한계</summary>
      <p>${escapeHtml(data.what_this_is)}</p>
      <ul>${gaps}</ul>
      <p class="curatedsrc">${(data.sources || []).map((s) =>
        `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.what)}</a>`).join(' · ')}</p>
    </details>`;
}
