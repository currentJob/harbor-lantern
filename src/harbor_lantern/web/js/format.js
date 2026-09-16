/* 표기 — 순수 함수만 (설계서 §6.15 · AC-031 · AC-032)
 *
 * 여기 있는 함수는 전부 입력만 보고 출력을 만든다. `Date.now()` 도, DOM 도 만지지 않는다.
 * 이유는 하나다 — AC-031 이 "고정된 UTC 시각을 주입하면 항상 같은 HH:MM 이 나온다"를
 * 요구하는데, `toLocaleTimeString({timeZone:'Asia/Hong_Kong'})` 은 브라우저 ICU 데이터에
 * 기대는 함수라 그 자리에서 검증할 수 없다. 홍콩은 UTC+8 고정·DST 없음(설계서 §2.3)이므로
 * 상수 오프셋 산술이 정확하고 검증 가능하다.
 */

/** 홍콩 표준시 오프셋(분). DST 없음 — 설계서 §2.3 / 가정 A9. */
export const HKT_OFFSET_MINUTES = 480;

const MS_PER_MINUTE = 60000;

function hktParts(utcMillis) {
  const shifted = new Date(utcMillis + HKT_OFFSET_MINUTES * MS_PER_MINUTE);
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
    hour: shifted.getUTCHours(),
    minute: shifted.getUTCMinutes(),
    weekday: (shifted.getUTCDay() + 6) % 7, // 0=월 … 6=일 (서버 규약과 동일)
  };
}

export function pad2(n) {
  return String(n).padStart(2, '0');
}

/** UTC 밀리초 → 홍콩 현지 'HH:MM' (24시간제). AC-031 의 검증 대상 함수. */
export function formatHkt(utcMillis) {
  const p = hktParts(utcMillis);
  return `${pad2(p.hour)}:${pad2(p.minute)}`;
}

/** UTC 밀리초 → 홍콩 현지 'YYYY-MM-DD'. */
export function formatHktDate(utcMillis) {
  const p = hktParts(utcMillis);
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}`;
}

/** UTC 밀리초 → 홍콩 현지 'MM.DD HH:MM' (마지막 갱신 시각 표시용). */
export function formatHktStamp(utcMillis) {
  const p = hktParts(utcMillis);
  return `${pad2(p.month)}.${pad2(p.day)} ${pad2(p.hour)}:${pad2(p.minute)}`;
}

/** 서버가 준 UTC ISO-8601 문자열 → 밀리초. 못 읽으면 null (화면이 NaN 을 뱉지 않게). */
export function parseIso(iso) {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

const WEEKDAY_KO = ['월', '화', '수', '목', '금', '토', '일'];

/** 'YYYY-MM-DD' → 'M.D(요일)'. 서버가 준 날짜 문자열만 쓴다(시간대 변환 없음). */
export function formatDateLabel(isoDate, weekday) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate || '');
  if (!m) return isoDate || '';
  const label = `${Number(m[2])}.${Number(m[3])}`;
  const wd = WEEKDAY_KO[weekday];
  return wd ? `${label}(${wd})` : label;
}

/** 거리 표기 — 원본 정적 페이지의 fmt() 와 같은 규칙 (AC-032).
 *  1000m 미만은 10m 단위 'NNNm', 10km 미만은 소수 1자리 km, 그 이상은 정수 km. */
export function formatDistance(meters) {
  if (meters == null || Number.isNaN(meters)) return '—';
  return meters < 1000
    ? `${Math.round(meters / 10) * 10}m`
    : `${(meters / 1000).toFixed(meters < 10000 ? 1 : 0)}km`;
}

/** 분 → '25분' · '1시간 25분'. */
export function formatMinutes(minutes) {
  if (minutes == null || Number.isNaN(minutes)) return '—';
  const m = Math.round(minutes);
  if (m < 60) return `${m}분`;
  const rest = m % 60;
  return rest === 0 ? `${Math.floor(m / 60)}시간` : `${Math.floor(m / 60)}시간 ${rest}분`;
}

/** HKD cent(정수) → 'HK$1,234.56'. 금액은 정수로만 다룬다 (NFR-014). */
export function formatHkd(minor) {
  if (minor == null) return '—';
  const sign = minor < 0 ? '-' : '';
  const abs = Math.abs(Math.trunc(minor));
  const won = Math.floor(abs / 100);
  const cent = abs % 100;
  return `${sign}HK$${groupDigits(won)}.${pad2(cent)}`;
}

/** 원(정수) → '17,123원'. 서버가 환산한 값을 그대로 찍는다 — 프론트는 환율 계산을 하지 않는다. */
export function formatKrw(krw) {
  if (krw == null) return null;
  return `${groupDigits(Math.trunc(krw))}원`;
}

export function groupDigits(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/** 'XXXXXXXXXXXX' → 'XXXX-XXXX-XXXX' (서버가 invite_code_display 를 주면 그것을 쓴다). */
export function formatInviteCode(code) {
  if (!code) return '';
  return code.replace(/(.{4})(?=.)/g, '$1-');
}

/** 텍스트를 HTML 에 끼워 넣기 전에 반드시 통과시킨다.
 *  스팟 이름·메모는 **사용자 입력**이다(REQ-004). 원본은 고정 배열이라 이스케이프가 없었지만,
 *  이제는 동행이 넣은 문자열이 그대로 innerHTML 에 들어간다. */
export function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 외부 링크 한 개. **스킴을 검사하고 만든다** — `javascript:` 가 href 에 들어갈 길을 막는다.
 *  URL 을 읽을 수 없으면 빈 문자열이다(링크가 아닌 것을 링크처럼 그리지 않는다).
 *  `explore.js` 에 있던 구현을 그대로 옮겼다 — 가이드 렌더러(`render/guide.js`)가 출처
 *  링크에 같은 규칙을 써야 하고(AC-080), 두 벌이면 한쪽만 고쳐지는 날이 온다. */
export function link(url, label) {
  try { const parsed = new URL(url); if (!['https:', 'http:'].includes(parsed.protocol)) return ''; }
  catch { return ''; }
  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} ↗</a>`;
}

/** 'HH:MM' + day_offset → '20:30' 또는 '+1일 01:10'. */
export function formatLocalTime(hhmm, dayOffset) {
  if (!hhmm) return '';
  return dayOffset ? `+${dayOffset}일 ${hhmm}` : hhmm;
}
