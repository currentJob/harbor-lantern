/* 영업시간 경고 · 일정 충돌 (설계서 §6.10 · §6.12 · AC-023 · AC-024 · AC-025)
 *
 * 서버가 만든 경고만 그린다. 프론트는 판정하지 않는다.
 * `hours.status === 'unknown'`(파싱 불가)에는 **경고가 없다** — 서버가 안 만든다.
 * 프론트가 "영업시간을 모르겠습니다"를 경고처럼 빨갛게 띄우면 그 순간 규칙이 무너진다.
 * 거짓 경고 하나가 진짜 경고 전부를 무시하게 만든다(R1).
 * 대신 카드의 영업시간 줄에 조용한 회색 배지로 표시한다(cards.js).
 */

import { escapeHtml } from '../format.js';

const KIND_LABEL = {
  closed_on_arrival: '도착 시각에 닫혀 있음',
  closed_day: '휴무일',
};

function nameOf(spotsById, spotId) {
  const spot = spotsById.get(spotId);
  return spot ? spot.name : spotId;
}

export function renderAlerts(container, { warnings, conflicts, day, spotsById }) {
  const dayIds = new Set((day ? day.spots : []).map((spot) => spot.id));
  const dayWarnings = (warnings || []).filter((w) => dayIds.has(w.spot_id));
  const dayConflicts = (conflicts || []).filter((c) => dayIds.has(c.spot_id) || dayIds.has(c.fixed_spot_id));

  if (!dayWarnings.length && !dayConflicts.length) {
    container.textContent = '';
    return;
  }

  const rows = [];
  for (const w of dayWarnings) {
    const label = KIND_LABEL[w.kind] || w.kind;
    const window = w.open_local && w.close_local
      ? ` <span class="who">(${escapeHtml(w.open_local)}–${escapeHtml(w.close_local)})</span>` : '';
    const eta = w.eta_local ? ` · 도착 ${escapeHtml(w.eta_local)}` : '';
    rows.push(
      `<div class="alert"><span>⚠</span><div><b>${escapeHtml(nameOf(spotsById, w.spot_id))}</b> · `
      + `${escapeHtml(label)}${eta}${window}<br>${escapeHtml(w.message || '')}</div></div>`,
    );
  }
  for (const c of dayConflicts) {
    rows.push(
      `<div class="alert conflict"><span>⏱</span><div>`
      + `<b>${escapeHtml(nameOf(spotsById, c.fixed_spot_id))}</b> 고정시각과 겹칩니다 — `
      + `<b>${escapeHtml(nameOf(spotsById, c.spot_id))}</b> 일정이 `
      + `${escapeHtml(c.overlap_start_local)}~${escapeHtml(c.overlap_end_local)}`
      + ` (${escapeHtml(String(c.overlap_minutes))}분) 침범합니다.</div></div>`,
    );
  }
  container.innerHTML = rows.join('');
}

/** 스팟 하나에 붙는 경고 배지들(카드 안에서 쓴다). */
export function warningBadges(warnings, spotId) {
  return (warnings || [])
    .filter((w) => w.spot_id === spotId)
    .map((w) => `<span class="badge warn">${escapeHtml(KIND_LABEL[w.kind] || w.kind)}</span>`)
    .join('');
}
