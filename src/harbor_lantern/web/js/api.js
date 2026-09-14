/* 서버 API 클라이언트 — contracts/openapi.yaml 이 SSoT (설계서 §6.15 · §7)
 *
 * 이 파일 밖에서는 `fetch` 를 부르지 않는다. 헤더(X-Participant-Token)·304 처리·
 * 에러 본문 해석이 한 곳에 있어야 화면 코드가 상태코드를 몰라도 되기 때문이다.
 *
 * 서버는 실패를 하나의 모양으로 준다: {error, message, detail?} (설계서 §7.2).
 * 그래서 ApiError 는 `code`(기계용)와 `message`(사람용)를 나눠 들고 다닌다.
 */

const DEFAULT_TIMEOUT_MS = 10000;
const API_BASE_KEY = 'hl_api_base';

/* API 주소는 **런타임에 바뀔 수 있어야 한다.**
 *
 * 화면을 GitHub Pages 에 올리고 백엔드는 PC 에서 도는 구성에서, 터널 주소는 재시작마다
 * 바뀐다. 주소를 내보내기 시점에 구워 버리면 그때마다 정적 파일을 다시 배포해야 한다.
 * 그래서 우선순위를 둔다: `?api=` 쿼리 → 이 브라우저에 저장된 값 → 빌드 시 config.js → 동일 출처.
 *
 * `?api=` 로 한 번 넣으면 저장되므로, 터널 주소가 바뀌어도 링크 한 번이면 된다.
 * https 만 받는다 — Pages 는 https 라 http 백엔드는 브라우저가 혼합 콘텐츠로 막는다.
 */
function normalizeBase(value) {
  if (!value) return null;
  let url;
  try { url = new URL(value); } catch { return null; }
  if (url.protocol !== 'https:' || url.username || url.password) return null;
  return url.origin;
}

function resolveApiBase() {
  let stored = null;
  try { stored = localStorage.getItem(API_BASE_KEY); } catch { /* 저장소가 막힌 브라우저 */ }

  const fromQuery = normalizeBase(new URLSearchParams(location.search).get('api'));
  if (fromQuery) {
    try { localStorage.setItem(API_BASE_KEY, fromQuery); } catch { /* 저장 실패는 치명적이지 않다 */ }
    return fromQuery;
  }
  return normalizeBase(stored)
    || (window.HARBOR_CONFIG?.apiBase || '').replace(/\/$/, '');
}

const API_BASE = resolveApiBase();

/** 현재 쓰는 백엔드 주소(동일 출처면 빈 문자열). 화면이 안내에 쓴다. */
export function apiBase() { return API_BASE; }

/** 백엔드 주소를 바꾸고 저장한다. 잘못된 값이면 false. */
export function setApiBase(value) {
  const base = normalizeBase(value);
  if (!base) return false;
  try { localStorage.setItem(API_BASE_KEY, base); } catch { /* 저장 실패해도 새로고침 전까진 동작 */ }
  return true;
}

export class ApiError extends Error {
  constructor(status, body, fallbackMessage) {
    const message = (body && body.message) || fallbackMessage || `요청이 실패했습니다 (HTTP ${status}).`;
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = (body && body.error) || 'http_error';
    this.detail = (body && body.detail) || null;
  }
}

export class NetworkError extends Error {
  constructor(cause) {
    super('서버에 연결하지 못했습니다. 네트워크나 서버 상태를 확인해 주세요.');
    this.name = 'NetworkError';
    this.status = 0;
    this.code = 'network_unreachable';
    this.cause = cause;
  }
}

/** 참가자 토큰은 기기 로컬 값이다 — 상태의 근원이 아니라 신원일 뿐이다(설계서 §6.15 · 가정 A10). */
const TOKEN_PREFIX = 'hl_token_';
const LAST_TRIP_KEY = 'hl_trip';

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null; // 사생활 보호 모드 등. 없으면 세션 한정으로 동작한다.
  }
}

const memoryStore = new Map();

export function readLocal(key) {
  const store = storage();
  if (!store) return memoryStore.has(key) ? memoryStore.get(key) : null;
  try {
    return store.getItem(key);
  } catch {
    return null;
  }
}

export function writeLocal(key, value) {
  const store = storage();
  if (!store) {
    if (value == null) memoryStore.delete(key);
    else memoryStore.set(key, value);
    return;
  }
  try {
    if (value == null) store.removeItem(key);
    else store.setItem(key, value);
  } catch {
    /* 저장 실패는 치명적이지 않다 — 이번 세션에서만 신원을 유지한다 */
  }
}

export const session = {
  tokenOf: (tripId) => readLocal(TOKEN_PREFIX + tripId),
  remember(tripId, token) {
    writeLocal(TOKEN_PREFIX + tripId, token);
    writeLocal(LAST_TRIP_KEY, tripId);
  },
  lastTripId: () => readLocal(LAST_TRIP_KEY),
  forget(tripId) {
    writeLocal(TOKEN_PREFIX + tripId, null);
    if (readLocal(LAST_TRIP_KEY) === tripId) writeLocal(LAST_TRIP_KEY, null);
  },
};

async function parseBody(response) {
  const type = response.headers.get('content-type') || '';
  if (!type.includes('json')) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/** 저수준 요청. 반환은 {status, headers, data}. 4xx/5xx 는 ApiError 로 던진다. */
async function request(path, { method = 'GET', body, token, headers = {}, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const init = {
    method,
    headers: { Accept: 'application/json', ...headers },
    signal: controller.signal,
  };
  if (token) init.headers['X-Participant-Token'] = token;
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(API_BASE + path, init);
  } catch (error) {
    throw new NetworkError(error);
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 304) return { status: 304, headers: response.headers, data: null };
  if (response.status === 204) return { status: 204, headers: response.headers, data: null };
  if (!response.ok) throw new ApiError(response.status, await parseBody(response));
  return { status: response.status, headers: response.headers, data: await parseBody(response) };
}

const trip = (tripId) => `/api/trips/${encodeURIComponent(tripId)}`;

export const api = {
  // ── 여행 · 참여 ────────────────────────────────────────────────────────
  async createTrip(payload) {
    const { data } = await request('/api/trips', { method: 'POST', body: payload || {} });
    return data;
  },
  async join(inviteCode, displayName) {
    const { data } = await request('/api/join', {
      method: 'POST',
      body: { invite_code: inviteCode, display_name: displayName },
    });
    return data;
  },
  async getTrip(tripId, token) {
    const { data } = await request(trip(tripId), { token });
    return data;
  },

  /** 상태 조회. ETag 를 주면 변경이 없을 때 304 를 받는다 (REQ-016 · AC-036).
   *  반환: {changed:boolean, etag:string|null, state:object|null} */
  async getState(tripId, token, etag) {
    const headers = etag ? { 'If-None-Match': etag } : {};
    const res = await request(`${trip(tripId)}/state`, { token, headers });
    if (res.status === 304) return { changed: false, etag, state: null };
    return { changed: true, etag: res.headers.get('ETag') || null, state: res.data };
  },

  // ── 스팟 ──────────────────────────────────────────────────────────────
  async createSpot(tripId, token, dayIndex, payload) {
    const { data } = await request(`${trip(tripId)}/days/${dayIndex}/spots`, {
      method: 'POST', token, body: payload,
    });
    return data;
  },
  async updateSpot(tripId, token, spotId, payload) {
    const { data } = await request(`${trip(tripId)}/spots/${encodeURIComponent(spotId)}`, {
      method: 'PATCH', token, body: payload,
    });
    return data;
  },
  async deleteSpot(tripId, token, spotId) {
    await request(`${trip(tripId)}/spots/${encodeURIComponent(spotId)}`, { method: 'DELETE', token });
  },
  async reorderDay(tripId, token, dayIndex, expectedRevision, spotIds) {
    const { data } = await request(`${trip(tripId)}/days/${dayIndex}/order`, {
      method: 'PUT', token, body: { expected_revision: expectedRevision, spot_ids: spotIds },
    });
    return data;
  },
  async moveSpot(tripId, token, spotId, expectedRevision, toDayIndex, toPosition = null) {
    const { data } = await request(`${trip(tripId)}/spots/${encodeURIComponent(spotId)}/move`, {
      method: 'POST', token,
      body: { expected_revision: expectedRevision, to_day_index: toDayIndex, to_position: toPosition },
    });
    return data;
  },
  async setDone(tripId, token, spotId, done) {
    const { data } = await request(`${trip(tripId)}/spots/${encodeURIComponent(spotId)}/done`, {
      method: 'PUT', token, body: { done },
    });
    return data;
  },
  async optimizeDay(tripId, token, dayIndex) {
    const { data } = await request(`${trip(tripId)}/days/${dayIndex}/optimize`, { method: 'POST', token });
    return data;
  },

  // ── 경비 · 정산 ────────────────────────────────────────────────────────
  async listExpenses(tripId, token) {
    const { data } = await request(`${trip(tripId)}/expenses`, { token });
    return data;
  },
  async createExpense(tripId, token, payload) {
    const { data } = await request(`${trip(tripId)}/expenses`, { method: 'POST', token, body: payload });
    return data;
  },
  async deleteExpense(tripId, token, expenseId) {
    await request(`${trip(tripId)}/expenses/${encodeURIComponent(expenseId)}`, { method: 'DELETE', token });
  },
  async getSettlement(tripId, token) {
    const { data } = await request(`${trip(tripId)}/settlement`, { token });
    return data;
  },

  // ── 외부 (실패해도 200 — 본문의 available/stale 로 표현된다) ───────────
  async getWeather() {
    const { data } = await request('/api/weather', { timeoutMs: 6000 });
    return data;
  },
  async getFx() {
    const { data } = await request('/api/fx', { timeoutMs: 6000 });
    return data;
  },

  /** 현재 위치 기준 근처 음식점·카페 (REQ-017).
   *  Overpass 는 집계 질의라 날씨·환율보다 느리다 — 타임아웃을 넉넉히 준다. */
  async getNearby({ lat, lng, radiusM }) {
    const params = new URLSearchParams({ lat, lng });
    if (radiusM) params.set('radius_m', radiusM);
    const { data } = await request(`/api/nearby?${params}`, { timeoutMs: 15000 });
    return data;
  },
};
