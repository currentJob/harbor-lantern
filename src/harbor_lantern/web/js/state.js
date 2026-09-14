/* 클라이언트 상태 보관 + 폴링 루프 (설계서 §6.14 DSN-21 · REQ-016)
 *
 * 원본 정적 페이지에서 상태의 근원은 localStorage 였다. 이제는 **서버**다.
 * localStorage 에 남는 것은 참가자 토큰뿐이고(api.js), 여기 있는 것은 전부
 * 서버 응답의 사본이거나 화면 전용 값(활성 일자·정렬·편집 모드)이다.
 *
 * 폴링은 10초(설계서 O3). 탭이 숨으면 멈추고, 돌아오면 즉시 1회 돈다.
 * 연속 실패에는 10 → 20 → 40 → 60초 백오프를 건다 — 서버가 죽었을 때
 * 초당 요청을 쏟아붓는 화면은 서버를 두 번 죽인다.
 */

import { api, session } from './api.js';

const POLL_INTERVAL_MS = 10000;
const POLL_MAX_INTERVAL_MS = 60000;
const EXTERNAL_REFRESH_MS = 15 * 60 * 1000; // 날씨 TTL 900초와 같은 주기

export const store = {
  tripId: null,
  token: null,
  state: null,          // GET /state 응답 전체
  etag: null,
  expenses: null,       // GET /expenses 응답
  settlement: null,     // GET /settlement 응답
  weather: null,
  fx: null,

  // 화면 전용
  activeDay: 1,
  sortByDistance: false,
  editMode: false,
  openSpotIds: new Set(),
  me: null,             // {lat, lng, acc}
  hint: '',
  syncError: null,

  // 근처 장소 (REQ-017) — 일정 상태와 **섞지 않는다.** 서버가 준 여행 데이터가 아니라
  // 그때그때 조회하는 휘발성 목록이고, 폴링 동기화 대상도 아니다.
  nearby: null,         // GET /api/nearby 응답
  nearbyBusy: false,
  nearbyMessage: '',
};

const listeners = new Set();

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function emit() {
  for (const fn of listeners) {
    try {
      fn(store);
    } catch (error) {
      // 렌더러 하나가 죽어도 나머지는 그린다. 화면 전체가 백지가 되는 것이 최악이다.
      console.error('렌더 중 오류', error);
    }
  }
}

export function setSession(tripId, token) {
  store.tripId = tripId;
  store.token = token;
  store.etag = null;
  session.remember(tripId, token);
}

/** 활성 일자: 서버가 준 days[].date 와 브라우저의 오늘(HKT)을 비교한다.
 *  원본은 월을 하드코딩했다(`y+"-9-5"`) — 데이터가 서버로 옮겨졌으므로 그럴 이유가 없다. */
export function pickActiveDay(state, todayHkt) {
  if (!state || !state.days || state.days.length === 0) return 1;
  const hit = state.days.find((day) => day.date === todayHkt);
  return hit ? hit.day_index : state.days[0].day_index;
}

export function activeDayState() {
  if (!store.state) return null;
  return store.state.days.find((day) => day.day_index === store.activeDay) || store.state.days[0] || null;
}

export function participantsById() {
  const map = new Map();
  for (const p of (store.state && store.state.participants) || []) map.set(p.id, p);
  return map;
}

export function allSpots() {
  const out = [];
  for (const day of (store.state && store.state.days) || []) {
    for (const spot of day.spots) out.push({ day, spot });
  }
  return out;
}

// ── 동기화 ────────────────────────────────────────────────────────────────
let pollTimer = null;
let currentInterval = POLL_INTERVAL_MS;
let failures = 0;
let inFlight = false;

/** 상태를 한 번 당겨온다. 변경이 없으면(304) 아무것도 다시 그리지 않는다. */
export async function syncOnce({ force = false } = {}) {
  if (!store.tripId || !store.token || inFlight) return false;
  inFlight = true;
  try {
    const result = await api.getState(store.tripId, store.token, force ? null : store.etag);
    failures = 0;
    currentInterval = POLL_INTERVAL_MS;
    store.syncError = null;
    if (!result.changed) return false;
    store.state = result.state;
    store.etag = result.etag;
    if (!store.state.days.some((day) => day.day_index === store.activeDay)) {
      store.activeDay = store.state.days.length ? store.state.days[0].day_index : 1;
    }
    emit();
    return true;
  } catch (error) {
    failures += 1;
    currentInterval = Math.min(POLL_MAX_INTERVAL_MS, POLL_INTERVAL_MS * 2 ** Math.min(failures - 1, 3));
    store.syncError = error;
    emit();
    return false;
  } finally {
    inFlight = false;
    schedulePoll();
  }
}

/** 경비·정산은 /state 에 없다(리비전 ETag 밖의 자원). 쓰기 뒤에만 다시 읽는다. */
export async function refreshLedger() {
  if (!store.tripId || !store.token) return;
  const [expenses, settlement] = await Promise.allSettled([
    api.listExpenses(store.tripId, store.token),
    api.getSettlement(store.tripId, store.token),
  ]);
  if (expenses.status === 'fulfilled') store.expenses = expenses.value;
  if (settlement.status === 'fulfilled') store.settlement = settlement.value;
  emit();
}

export async function refreshExternal() {
  const [weather, fx] = await Promise.allSettled([api.getWeather(), api.getFx()]);
  // 실패해도 화면은 산다 — 위젯만 '불러오지 못함'으로 바뀐다(NFR-004).
  store.weather = weather.status === 'fulfilled' ? weather.value : { available: false, stale: false, fetched_at: null };
  store.fx = fx.status === 'fulfilled' ? fx.value : { available: false, stale: false, fetched_at: null };
  emit();
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (document.visibilityState === 'hidden') return; // 숨은 탭은 폴링하지 않는다
  pollTimer = setTimeout(() => { syncOnce(); }, currentInterval);
}

export function startSync() {
  schedulePoll();
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') syncOnce();
    else clearTimeout(pollTimer);
  });
  setInterval(() => {
    if (document.visibilityState === 'visible') refreshExternal();
  }, EXTERNAL_REFRESH_MS);
}

export function stopSync() {
  clearTimeout(pollTimer);
}
