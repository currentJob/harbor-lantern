/* 스팟 카드 목록 (설계서 §6.15 · AC-032~AC-034 · REQ-004~006 · REQ-010~013)
 *
 * 원본 정적 페이지의 카드를 그대로 계승한다 — 접힘/펼침, 설명·추천, 거리, 길찾기,
 * 완료 체크. 새로 붙은 것은 서버가 계산해 주는 것들이다: 도착 예상시각(schedule),
 * 다음 구간 이동(leg_to_next), 누가 언제 체크했는지(done), 그리고 편집 컨트롤.
 *
 * 완료 체크는 이제 기기가 아니라 서버에 저장된다 — 그래서 체크 표시 옆에 사람 이름이 붙는다.
 */

import {
  escapeHtml, formatDistance, formatLocalTime, formatMinutes,
} from '../format.js';
import { haversineMeters, sortByDistance } from '../geo.js';
import { warningBadges } from './warnings.js';
import { routeHtml } from './reviewplan.js';

const MODE_LABEL = { walk: '도보', transit: '대중교통' };

function hoursLine(spot) {
  const parts = [];
  parts.push(`🕐 ${escapeHtml(spot.hours_text || '영업시간 정보 없음')}`);
  if (spot.closed_text) parts.push(`<span class="cl">${escapeHtml(spot.closed_text)}</span>`);
  if (spot.hours && spot.hours.approximate) parts.push('<span class="approx">근사치</span>');
  // unknown 은 경고가 아니다. 조용한 회색 배지 하나로만 알린다(AC-024).
  if (spot.hours && spot.hours.status === 'unknown') {
    parts.push('<span class="badge">영업시간 확인 필요</span>');
  }
  return `<div class="hours">${parts.join(' ')}</div>`;
}

function doneLine(spot) {
  if (!spot.done || !spot.done.is_done) return '';
  const who = spot.done.by_display_name ? escapeHtml(spot.done.by_display_name) : '동행';
  return `<span class="badge done">${who} 완료</span>`;
}

function legLine(leg, nextName) {
  if (!leg) return '';
  const mode = MODE_LABEL[leg.mode] || leg.mode;
  return `<div class="leg">↓ ${escapeHtml(mode)} ${escapeHtml(formatMinutes(leg.minutes))} · `
    + `${escapeHtml(formatDistance(leg.distance_m))}`
    + `${nextName ? ` → ${escapeHtml(nextName)}` : ''}`
    + `${leg.estimated ? ' <span class="est">(예상)</span>' : ''}</div>`;
}

function editRow(spot, ctx, index, count) {
  if (!ctx.editMode) return '';
  const reorderable = !ctx.sortByDistance;
  const dayOptions = (ctx.days || [])
    .map((day) => `<option value="${day.day_index}"${day.day_index === ctx.day.day_index ? ' selected' : ''}>`
      + `Day ${day.day_index}로 이동</option>`)
    .join('');
  return '<div class="editrow">'
    + `<button class="mini" data-act="up" ${reorderable && index > 0 ? '' : 'disabled'}>▲ 위로</button>`
    + `<button class="mini" data-act="down" ${reorderable && index < count - 1 ? '' : 'disabled'}>▼ 아래로</button>`
    + '<button class="mini" data-act="edit">✎ 수정</button>'
    + '<button class="mini danger" data-act="delete">🗑 삭제</button>'
    + `<select class="mini" data-act="move" aria-label="일자 이동">${dayOptions}</select>`
    + '</div>';
}

/**
 * @param {HTMLElement} container
 * @param {object} ctx  {day, days, me, sortByDistance, editMode, openSpotIds, warnings, actions}
 */
export function renderCards(container, ctx) {
  container.textContent = '';
  const day = ctx.day;
  if (!day) return;
  if (!day.spots.length) {
    container.innerHTML = '<div class="empty">이 날의 일정이 비어 있습니다. ‘편집’을 켜고 스팟을 추가해 보세요.</div>';
    return;
  }

  const ordered = ctx.sortByDistance && ctx.me
    ? sortByDistance(day.spots, ctx.me, (spot) => ({ lat: spot.lat, lng: spot.lng }))
    : day.spots.slice();

  ordered.forEach((spot, renderIndex) => {
    const scheduleIndex = day.spots.indexOf(spot);
    const distance = ctx.me ? haversineMeters(ctx.me, { lat: spot.lat, lng: spot.lng }) : null;
    const isOpen = ctx.openSpotIds.has(spot.id);
    const isDone = !!(spot.done && spot.done.is_done);
    const eta = spot.schedule
      ? `<span class="eta">도착 ${escapeHtml(formatLocalTime(spot.schedule.eta_local, spot.schedule.eta_day_offset))}</span>`
      : '';

    const card = document.createElement('div');
    card.className = `card${isOpen ? ' open' : ''}`;
    card.style.setProperty('--c', day.color);
    card.dataset.done = isDone ? '1' : '0';
    card.dataset.spotId = spot.id;
    card.style.animationDelay = `${Math.min(renderIndex, 12) * 0.03}s`;
    card.innerHTML =
      '<div class="row1">'
        + `<span class="time">${escapeHtml(spot.time_label)}</span>${eta}`
        + `<span class="sub">${scheduleIndex + 1}. ${escapeHtml(spot.name_original || '')}</span>`
        + '<span class="more">▾</span>'
      + '</div>'
      + `<div class="name">${escapeHtml(spot.name)} ${doneLine(spot)}</div>`
      + (spot.tip ? `<div class="tip">${escapeHtml(spot.tip)}</div>` : '')
      + hoursLine(spot)
      + (warningBadges(ctx.warnings, spot.id) ? `<div class="hours">${warningBadges(ctx.warnings, spot.id)}</div>` : '')
      + '<div class="detail">'
        + (spot.description ? `<div class="desc">${escapeHtml(spot.description)}</div>` : '')
        + (spot.recommendation ? `<div class="rec"><b>추천</b> · ${escapeHtml(spot.recommendation)}</div>` : '')
        + editRow(spot, ctx, scheduleIndex, day.spots.length)
      + '</div>'
      + '<div class="foot">'
        + `<span class="dist${distance == null ? ' off' : ''}">`
        + `${distance == null ? '· 위치 꺼짐' : `📍 ${escapeHtml(formatDistance(distance))}`}</span>`
        + '<span class="spacer"></span>'
        + `<a class="go" href="${escapeHtml(spot.directions_url)}" target="_blank" rel="noopener">길찾기 ›</a>`
        + `<button class="chk${isDone ? ' done' : ''}" type="button" aria-label="완료 체크"`
        + ` aria-pressed="${isDone}">${isDone ? '✓' : ''}</button>`
      + '</div>';

    card.addEventListener('click', (event) => {
      if (event.target.closest('a,button,select')) return;
      ctx.actions.toggleOpen(spot.id);
      ctx.actions.focus(spot);
    });
    card.querySelector('.chk').addEventListener('click', (event) => {
      event.stopPropagation();
      ctx.actions.toggleDone(spot);
    });
    const row = card.querySelector('.editrow');
    if (row) {
      row.querySelector('[data-act="up"]').addEventListener('click', () => ctx.actions.reorder(spot, -1));
      row.querySelector('[data-act="down"]').addEventListener('click', () => ctx.actions.reorder(spot, +1));
      row.querySelector('[data-act="edit"]').addEventListener('click', () => ctx.actions.edit(spot));
      row.querySelector('[data-act="delete"]').addEventListener('click', () => ctx.actions.remove(spot));
      row.querySelector('[data-act="move"]').addEventListener('change', (event) => {
        const target = Number(event.target.value);
        if (target !== day.day_index) ctx.actions.moveToDay(spot, target);
      });
    }
    container.appendChild(card);

    // 이동 구간은 '일정 순서'일 때만 뜻이 있다. 가까운 순으로 보고 있을 때는 감춘다.
    if (!ctx.sortByDistance && spot.leg_to_next) {
      const next = day.spots[scheduleIndex + 1];
      const leg = document.createElement('div');
      leg.innerHTML = legLine(spot.leg_to_next, next ? next.name : '');
      if (next) leg.firstElementChild.insertAdjacentHTML('beforeend', routeHtml(spot, next));
      container.appendChild(leg.firstElementChild);
    }
  });
}

const FIELDS = [
  ['name', '이름', 'text', true],
  ['name_original', '원어·부제', 'text', false],
  ['time_label', '시간대 (오전·저녁·20:00 …)', 'text', false],
  ['tip', '한 줄 소개', 'text', false],
  ['hours_text', '영업시간 (자유 입력)', 'text', false],
  ['closed_text', '휴무', 'text', false],
  ['lat', '위도', 'number', true],
  ['lng', '경도', 'number', true],
  ['dwell_minutes', '체류시간(분) — 비우면 시간대 기본값', 'number', false],
];

/** 스팟 추가·수정 폼. 반환값 없음 — 제출 결과는 onSubmit 이 받는다. */
export function renderSpotForm(container, { spot, dayIndex, onSubmit, onCancel }) {
  const value = (key) => (spot && spot[key] != null ? String(spot[key]) : '');
  const inputs = FIELDS.map(([key, label, type, required]) =>
    `<label class="field"><span>${escapeHtml(label)}${required ? ' *' : ''}</span>`
    + `<input name="${key}" type="${type}" ${type === 'number' ? 'step="any"' : ''} `
    + `value="${escapeHtml(value(key))}" ${required ? 'required' : ''}></label>`).join('');

  container.innerHTML = '<form class="form" novalidate>'
    + `<h3>${spot ? '스팟 수정' : `Day ${dayIndex} 스팟 추가`}</h3>`
    + inputs
    + '<label class="field"><span>설명</span>'
    + `<textarea name="description">${escapeHtml(value('description'))}</textarea></label>`
    + '<label class="field"><span>추천</span>'
    + `<textarea name="recommendation">${escapeHtml(value('recommendation'))}</textarea></label>`
    + '<div class="formbtns"><button class="primary" type="submit">저장</button>'
    + '<button class="secondary" type="button" data-act="cancel">취소</button></div>'
    + '</form>';

  const form = container.querySelector('form');
  form.querySelector('[data-act="cancel"]').addEventListener('click', onCancel);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const payload = {};
    for (const [key, , type] of FIELDS) {
      const raw = String(data.get(key) || '').trim();
      if (type === 'number') {
        if (raw === '') {
          if (key === 'dwell_minutes') payload[key] = null;
          continue;
        }
        payload[key] = Number(raw);
      } else if (raw !== '' || spot) {
        payload[key] = raw;
      }
    }
    payload.description = String(data.get('description') || '').trim();
    payload.recommendation = String(data.get('recommendation') || '').trim();
    onSubmit(payload);
  });
}
