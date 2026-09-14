/* 일자 탭 · 일자 요약 (설계서 §6.15 — 원본 자산 계승)
 *
 * 탭의 색은 일자 색(`--c`)이고, 활성 탭은 그 색으로 물든다. 원본이 그랬고,
 * 그것이 "지금 몇째 날을 보고 있는지"를 글자 없이 알려 주는 장치다.
 */

import { escapeHtml, formatDateLabel, formatDistance, formatMinutes } from '../format.js';

export function renderTabs(container, days, activeDay, onSelect) {
  container.textContent = '';
  for (const day of days || []) {
    const button = document.createElement('button');
    button.className = 'tab';
    button.type = 'button';
    button.style.setProperty('--c', day.color);
    button.dataset.active = day.day_index === activeDay ? '1' : '0';
    button.dataset.dayIndex = String(day.day_index);
    button.setAttribute('aria-pressed', day.day_index === activeDay ? 'true' : 'false');
    button.innerHTML = `Day ${day.day_index}<small>${escapeHtml(formatDateLabel(day.date, day.weekday))}</small>`;
    button.addEventListener('click', () => onSelect(day.day_index));
    container.appendChild(button);
  }
}

export function renderDaySummary(container, day) {
  if (!day) {
    container.textContent = '';
    return;
  }
  container.style.setProperty('--c', day.color);
  const totals = day.totals || { distance_m: 0, travel_minutes: 0, dwell_minutes: 0 };
  container.innerHTML =
    `<h2>${escapeHtml(day.title)}</h2>`
    + `<span class="accent">${escapeHtml(day.area)}</span>`
    + `<span>${day.spots.length}곳 · 이동 ${escapeHtml(formatDistance(totals.distance_m))}`
    + ` · ${escapeHtml(formatMinutes(totals.travel_minutes))}`
    + ` · 체류 ${escapeHtml(formatMinutes(totals.dwell_minutes))}</span>`
    + `<span>시작 ${escapeHtml(day.start_local)}</span>`;
}
