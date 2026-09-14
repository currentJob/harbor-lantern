/* 헤더 상황판 — 홍콩 현지시각 시계 · 여행 날짜 · 날씨 · 환율
 * (설계서 §6.15 · AC-031 · REQ-014 · REQ-015)
 *
 * 시각은 **클라이언트가** 계산한다. /state 응답에 "지금"이 들어가면 ETag 가 거짓말을
 * 하기 때문이다(설계서 §6.14 함정 F1). 그래서 시계는 서버를 부르지 않는다.
 *
 * 날씨·환율에서 가장 중요한 규칙: **stale 이면 마지막 갱신 시각을 함께 보인다.**
 * 낡은 값을 최신인 척 보여 주는 것이 최악이다 — 틀렸다는 것조차 안 보인다.
 */

import { escapeHtml, formatDateLabel, formatHkt, formatHktStamp, parseIso } from '../format.js';

const TICK_MS = 20000; // 원본과 같은 주기

export function startClock(now = () => Date.now()) {
  const tick = () => {
    const element = document.getElementById('clock');
    if (element) element.innerHTML = `홍콩 <b>${formatHkt(now())}</b>`;
  };
  tick();
  return setInterval(tick, TICK_MS);
}

export function renderTripDates(days) {
  const element = document.getElementById('tripdates');
  if (!element) return;
  if (!days || !days.length) {
    element.textContent = '—';
    return;
  }
  const first = days[0];
  const last = days[days.length - 1];
  element.textContent = `${formatDateLabel(first.date, first.weekday)} → ${formatDateLabel(last.date, last.weekday)}`;
}

// WMO 날씨 코드 → 이모지·한국어. Open-Meteo 가 주는 코드다(설계서 §6.13).
const WMO = [
  [[0], '☀️', '맑음'],
  [[1, 2], '🌤', '구름 조금'],
  [[3], '☁️', '흐림'],
  [[45, 48], '🌫', '안개'],
  [[51, 53, 55, 56, 57], '🌦', '이슬비'],
  [[61, 63, 65, 66, 67], '🌧', '비'],
  [[71, 73, 75, 77, 85, 86], '🌨', '눈'],
  [[80, 81, 82], '🌧', '소나기'],
  [[95, 96, 99], '⛈', '뇌우'],
];

function describeWeatherCode(code) {
  for (const [codes, icon, label] of WMO) {
    if (codes.includes(code)) return { icon, label };
  }
  return { icon: '🌡', label: '' };
}

function ageSuffix(meta) {
  const ms = parseIso(meta && meta.fetched_at);
  if (ms == null) return '';
  return ` <span class="age">· 마지막 갱신 ${escapeHtml(formatHktStamp(ms))}</span>`;
}

function weatherChip(weather) {
  if (!weather || !weather.available) {
    return '<span class="chip off">🌡 날씨를 불러오지 못했습니다</span>';
  }
  const { icon, label } = describeWeatherCode(weather.weather_code);
  const parts = [];
  if (weather.temp_c != null) parts.push(`<b>${escapeHtml(weather.temp_c.toFixed(1))}℃</b>`);
  if (label) parts.push(escapeHtml(label));
  if (weather.today_min_c != null && weather.today_max_c != null) {
    parts.push(`${escapeHtml(weather.today_min_c.toFixed(0))}~${escapeHtml(weather.today_max_c.toFixed(0))}℃`);
  }
  if (weather.precip_prob_pct != null) parts.push(`강수 ${escapeHtml(String(weather.precip_prob_pct))}%`);
  const stale = weather.stale ? ' stale' : '';
  return `<span class="chip${stale}">${icon} ${parts.join(' · ')}${weather.stale ? ageSuffix(weather) : ''}</span>`;
}

function fxChip(fx) {
  if (!fx || !fx.available || fx.rate_micro == null) {
    return '<span class="chip off">💱 환율을 불러오지 못했습니다</span>';
  }
  const rate = (fx.rate_micro / 1000000).toFixed(2);
  // rate_date 는 '고장'이 아니라 공급자의 환율 기준일이다(주말이면 직전 영업일). stale 과 섞지 않는다.
  const basis = fx.rate_date ? ` <span class="age">(${escapeHtml(fx.rate_date)} 기준)</span>` : '';
  const stale = fx.stale ? ' stale' : '';
  return `<span class="chip${stale}">💱 HK$1 = <b>${escapeHtml(rate)}원</b>${basis}${fx.stale ? ageSuffix(fx) : ''}</span>`;
}

export function renderStatusStrip(container, { weather, fx, syncError }) {
  const chips = [weatherChip(weather), fxChip(fx)];
  if (syncError) {
    chips.push(`<span class="chip off">⚠ 동기화 실패 — ${escapeHtml(syncError.message || '재시도 중')}</span>`);
  }
  container.innerHTML = chips.join('');
}
