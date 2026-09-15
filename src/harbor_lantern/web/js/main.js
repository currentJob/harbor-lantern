/* 부트스트랩 · 화면 조립 (설계서 §6.15 DSN-22)
 *
 * 순서: 저장된 토큰 확인 → (없으면) 참여 게이트 → 첫 /state → 렌더 → 폴링 시작.
 *
 * 이 파일이 지키는 약속 하나: **서버가 없어도 화면은 죽지 않는다.**
 * API 가 응답하지 않으면 빈 화면 대신 이유와 다시 시도 버튼을 보여 준다.
 * 지도 역시 없어도 되는 부품이라 목록·계산은 그대로 돈다(NFR-015 · R4).
 */

import { ApiError, api, apiBase, readLocal, session, setApiBase, writeLocal } from './api.js';
import { escapeHtml, formatDistance, formatHktDate } from './format.js';
import { LocationTracker } from './geo.js';
import { TripMap } from './map.js';
import { renderCards, renderSpotForm } from './render/cards.js';
import { renderStatusStrip, renderTripDates, startClock } from './render/clock.js';
import { renderExpenses } from './render/expenses.js';
import { renderCurated } from './render/curated.js';
import { renderNearby } from './render/nearby.js';
import { renderProgress } from './render/progress.js';
import { renderSettlement } from './render/settlement.js';
import { renderDaySummary, renderTabs } from './render/tabs.js';
import { renderAlerts } from './render/warnings.js';
import {
  activeDayState, allSpots, emit, pickActiveDay, refreshExternal, refreshLedger,
  setSession, startSync, store, subscribe, syncOnce,
} from './state.js';

const el = (id) => document.getElementById(id);

// 화면 전용 상태 — 서버에 없고 저장할 필요도 없는 것들.
const ui = {
  editingSpot: null,     // 수정 중인 스팟
  addingSpot: false,     // 추가 폼 열림
  proposal: null,        // 동선 최적화 제안
  expenseFormOpen: false,
  meId: null,            // 내 참가자 id
  mapRevision: null,     // 마커를 마지막으로 그린 리비전
};

let tripMap = null;
let tracker = null;

// ── 알림 ──────────────────────────────────────────────────────────────────
let toastTimer = null;
function toast(message, bad = false) {
  const node = el('toast');
  node.textContent = message;
  node.classList.toggle('bad', bad);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 3200);
}

/** 쓰기 동작 공통 껍데기. 실패를 화면에 남기고, 충돌이면 최신 상태를 다시 당긴다. */
async function run(action, successMessage) {
  try {
    await action();
    if (successMessage) toast(successMessage);
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      toast('다른 동행이 먼저 바꿨습니다. 최신 상태를 불러왔어요.', true);
      await syncOnce({ force: true });
      return false;
    }
    toast(error.message || '요청이 실패했습니다.', true);
    return false;
  }
}

// ── 참여 게이트 ───────────────────────────────────────────────────────────
function showGate(message) {
  el('gate').hidden = false;
  el('app').hidden = true;
  const box = el('gateError');
  if (message) {
    box.innerHTML = escapeHtml(message);
    box.hidden = false;
    if (session.lastTripId()) {
      const retry = document.createElement('button');
      retry.className = 'secondary';
      retry.type = 'button';
      retry.style.marginTop = '10px';
      retry.textContent = '다시 시도';
      retry.addEventListener('click', () => { box.hidden = true; boot(); });
      box.appendChild(retry);
    }
  } else {
    box.hidden = true;
  }
}

/* 백엔드 주소 입력 — Pages + 로컬 백엔드 구성에서 터널 주소가 바뀔 때 쓴다.
   같은 출처에서 서빙되는 평소에는 접혀 있고 "이 사이트"라고만 알린다. */
function wireBackendBox() {
  const base = apiBase();
  const now = el('backendNow');
  const box = el('backendBox');
  now.textContent = base ? `· ${base.replace(/^https:\/\//, '')}` : '· 이 사이트';
  el('backendInput').value = base;

  // 동일 출처가 아닌데 아직 주소가 없으면(= Pages 에 그냥 올라온 경우) 펼쳐서 먼저 묻는다.
  if (!base && location.protocol === 'https:' && !location.hostname.match(/^(localhost|127\.)/)) {
    box.open = true;
  }

  el('backendBtn').addEventListener('click', () => {
    const value = el('backendInput').value.trim();
    if (!setApiBase(value)) {
      toast('https:// 로 시작하는 서버 주소를 입력하세요.', true);
      return;
    }
    location.reload();
  });
}

function wireGate() {
  wireBackendBox();

  el('createBtn').addEventListener('click', async () => {
    const name = el('displayName').value.trim();
    await run(async () => {
      const created = await api.createTrip(name ? { organizer_display_name: name } : {});
      adoptSession(created.trip.id, created.participant_token, created.participant.id);
      await afterJoin();
    }, '새 여행을 만들었습니다.');
  });

  el('joinBtn').addEventListener('click', async () => {
    const code = el('inviteCode').value.trim();
    const name = el('displayName').value.trim();
    if (!code) { toast('초대코드를 입력하세요.', true); return; }
    if (!name) { toast('표시명을 입력하세요.', true); return; }
    await run(async () => {
      const joined = await api.join(code, name);
      adoptSession(joined.trip_id, joined.participant_token, joined.participant.id);
      await afterJoin();
    }, '여행에 참여했습니다.');
  });
}

function adoptSession(tripId, token, participantId) {
  setSession(tripId, token);
  ui.meId = participantId;
  writeLocal(`hl_me_${tripId}`, participantId);
}

async function afterJoin() {
  el('gate').hidden = true;
  el('app').hidden = false;
  await loadEverything();
}

// ── 로딩 ──────────────────────────────────────────────────────────────────
async function loadEverything() {
  const ok = await syncOnce({ force: true });
  if (!ok && !store.state) {
    showGate(store.syncError ? store.syncError.message : '여행 상태를 불러오지 못했습니다.');
    return;
  }
  store.activeDay = pickActiveDay(store.state, formatHktDate(Date.now()));
  if (!tripMap) initMap();
  emit();
  refreshLedger();
  refreshExternal();
  startSync();
}

function initMap() {
  tripMap = new TripMap('map', {
    onMarkerClick: (dayIndex, spotId) => {
      store.activeDay = dayIndex;
      store.openSpotIds.add(spotId);
      emit();
    },
    onTileTrouble: (message) => {
      const node = el('mapmsg');
      node.textContent = message;
      node.hidden = false;
    },
  });
  tripMap.init();
}

// ── 지도·위치 바 ──────────────────────────────────────────────────────────
/* ── 미쉐린 큐레이션 목록 (REQ-019) ────────────────────────────────────── */

function clearCurated() {
  store.curated = null;
  store.curatedMessage = '';
  store.curatedBusy = false;
  emit();
}

async function loadCurated() {
  // 위치는 **있으면 쓰고 없으면 그냥 목록으로 본다.** 근처 검색과 달리 위치가
  // 필수가 아니다 — 저장된 목록이라 좌표 없이도 보여 줄 것이 있다.
  store.curatedBusy = true;
  store.curatedMessage = '';
  emit();
  try {
    const origin = store.me ? { lat: store.me.lat, lng: store.me.lng } : {};
    store.curated = await api.getCurated(origin);
  } catch (error) {
    store.curated = null;
    store.curatedMessage = error.message || '목록을 불러오지 못했습니다.';
  } finally {
    store.curatedBusy = false;
    emit();
  }
}

/* ── 근처 음식점·카페 (REQ-017 · REQ-018) ──────────────────────────────── */

function clearNearby() {
  store.nearby = null;
  store.nearbyMessage = '';
  store.nearbyBusy = false;
  if (tripMap) tripMap.clearNearby();
  emit();
}

async function loadNearby() {
  // 좌표 없이 부르면 서버가 422 로 거절한다 — 그 전에 사람 말로 안내한다.
  if (!store.me) {
    store.nearbyMessage = '먼저 ‘내 위치’를 켜 주세요. 지금 서 있는 자리를 알아야 근처를 찾습니다.';
    emit();
    return;
  }
  store.nearbyBusy = true;
  store.nearbyMessage = '';
  emit();
  try {
    store.nearby = await api.getNearby({ lat: store.me.lat, lng: store.me.lng });
  } catch (error) {
    // 조회 실패가 앱을 멈추면 안 된다(NFR-004 의 화면 쪽 귀결).
    store.nearby = null;
    store.nearbyMessage = error.message || '근처 정보를 가져오지 못했습니다.';
  } finally {
    store.nearbyBusy = false;
    emit();
  }
}

async function addNearbyToItinerary(place) {
  await run(async () => {
    await api.createSpot(store.tripId, store.token, store.activeDay, {
      name: place.name,
      lat: place.lat,
      lng: place.lng,
      time_label: '점심',
      tip: `근처 검색으로 추가 · ${place.category_label}`,
    });
    await syncOnce({ force: true });
  }, `${place.name} 을(를) Day ${store.activeDay} 에 추가했습니다.`);
}

function wireMapBar() {
  tracker = new LocationTracker({
    onUpdate: (me) => {
      store.me = me;
      el('locbtn').classList.add('live');
      store.hint = `실시간 위치 추적 중 (정확도 ±${Math.round(me.acc)}m). 카드에 남은 거리가 표시됩니다.`;
      if (tripMap) tripMap.showMe(me);
      emit();
    },
    onError: (error) => {
      el('locbtn').classList.remove('live');
      store.hint = error.message;
      emit();
    },
  });

  el('locbtn').addEventListener('click', () => {
    if (tracker.tracking && store.me) {
      if (tripMap) tripMap.flyToMe(store.me);
      return;
    }
    store.hint = '위치 권한을 확인하는 중…';
    emit();
    tracker.start();
  });

  el('sortbtn').addEventListener('click', () => {
    if (!store.me) {
      store.hint = '먼저 ‘내 위치’를 켜면 가까운 순으로 정렬됩니다.';
      emit();
      return;
    }
    store.sortByDistance = !store.sortByDistance;
    emit();
  });

  el('curatedbtn').addEventListener('click', () => {
    if (store.curated || store.curatedMessage) { clearCurated(); return; }
    loadCurated();
  });

  el('nearbtn').addEventListener('click', () => {
    // 이미 결과가 떠 있으면 닫는다 — 목록이 화면을 계속 차지하면 일정이 안 보인다.
    if (store.nearby || store.nearbyMessage) {
      clearNearby();
      return;
    }
    loadNearby();
  });

  el('editbtn').addEventListener('click', () => {
    store.editMode = !store.editMode;
    ui.editingSpot = null;
    ui.addingSpot = false;
    ui.proposal = null;
    emit();
  });
}

// ── 스팟 동작 ─────────────────────────────────────────────────────────────
const actions = {
  toggleOpen(spotId) {
    if (store.openSpotIds.has(spotId)) store.openSpotIds.delete(spotId);
    else store.openSpotIds.add(spotId);
    emit();
  },
  focus(spot) {
    if (tripMap) tripMap.focus(spot);
  },
  async toggleDone(spot) {
    const next = !(spot.done && spot.done.is_done);
    await run(async () => {
      await api.setDone(store.tripId, store.token, spot.id, next);
      await syncOnce({ force: true });
    });
  },
  async reorder(spot, delta) {
    const day = activeDayState();
    const ids = day.spots.map((s) => s.id);
    const from = ids.indexOf(spot.id);
    const to = from + delta;
    if (to < 0 || to >= ids.length) return;
    [ids[from], ids[to]] = [ids[to], ids[from]];
    await run(async () => {
      await api.reorderDay(store.tripId, store.token, day.day_index, store.state.trip.revision, ids);
      await syncOnce({ force: true });
    }, '순서를 바꿨습니다.');
  },
  edit(spot) {
    ui.editingSpot = spot;
    ui.addingSpot = false;
    emit();
  },
  async remove(spot) {
    if (!window.confirm(`‘${spot.name}’ 을(를) 삭제할까요?`)) return;
    await run(async () => {
      await api.deleteSpot(store.tripId, store.token, spot.id);
      await syncOnce({ force: true });
    }, '삭제했습니다.');
  },
  async moveToDay(spot, dayIndex) {
    await run(async () => {
      await api.moveSpot(store.tripId, store.token, spot.id, store.state.trip.revision, dayIndex);
      await syncOnce({ force: true });
    }, `Day ${dayIndex}로 옮겼습니다.`);
  },
};

// ── 일자 편집 바(추가 · 최적화) ───────────────────────────────────────────
function renderDayEdit(container, day) {
  container.textContent = '';
  if (!store.editMode || !day) return;

  if (ui.editingSpot || ui.addingSpot) {
    renderSpotForm(container, {
      spot: ui.editingSpot,
      dayIndex: day.day_index,
      onCancel: () => { ui.editingSpot = null; ui.addingSpot = false; emit(); },
      onSubmit: async (payload) => {
        const editing = ui.editingSpot;
        const ok = await run(async () => {
          if (editing) {
            await api.updateSpot(store.tripId, store.token, editing.id, { ...payload, version: editing.version });
          } else {
            await api.createSpot(store.tripId, store.token, day.day_index, payload);
          }
          await syncOnce({ force: true });
        }, editing ? '수정했습니다.' : '추가했습니다.');
        if (ok) { ui.editingSpot = null; ui.addingSpot = false; emit(); }
      },
    });
    return;
  }

  const add = document.createElement('button');
  add.className = 'mini';
  add.type = 'button';
  add.textContent = '＋ 스팟 추가';
  add.addEventListener('click', () => { ui.addingSpot = true; emit(); });
  container.appendChild(add);

  const optimize = document.createElement('button');
  optimize.className = 'mini';
  optimize.type = 'button';
  optimize.textContent = '동선 최적화 제안';
  optimize.addEventListener('click', async () => {
    await run(async () => {
      ui.proposal = await api.optimizeDay(store.tripId, store.token, day.day_index);
      emit();
    });
  });
  container.appendChild(optimize);

  if (ui.proposal && ui.proposal.day_index === day.day_index) {
    container.appendChild(proposalPanel(day, ui.proposal));
  }
}

function proposalPanel(day, proposal) {
  const saved = proposal.current_total_distance_m - proposal.proposed_total_distance_m;
  const nameOf = (id) => {
    const spot = day.spots.find((s) => s.id === id);
    return spot ? spot.name : id;
  };
  const panel = document.createElement('div');
  panel.className = 'form';
  panel.style.width = '100%';
  panel.innerHTML = '<h3>동선 최적화 제안</h3>'
    + `<div class="memo">현재 ${escapeHtml(formatDistance(proposal.current_total_distance_m))}`
    + ` → 제안 ${escapeHtml(formatDistance(proposal.proposed_total_distance_m))}`
    + `${proposal.improved ? ` (${escapeHtml(formatDistance(saved))} 단축)` : ' — 더 짧은 순서를 찾지 못했습니다'}</div>`
    + `<div class="memo" style="margin-top:8px">제안 순서: ${
      proposal.proposed_order.map((id, i) => `${i + 1}. ${escapeHtml(nameOf(id))}`).join(' → ')}</div>`
    + (proposal.anchored_spot_ids.length
      ? `<div class="memo" style="margin-top:6px">고정시각 스팟은 자리를 유지합니다: ${
        proposal.anchored_spot_ids.map((id) => escapeHtml(nameOf(id))).join(', ')}</div>` : '')
    + '<div class="formbtns" style="margin-top:12px">'
    + `<button class="primary" type="button" data-act="apply"${proposal.improved ? '' : ' disabled'}>이 순서로 적용</button>`
    + '<button class="secondary" type="button" data-act="close">닫기</button></div>';

  panel.querySelector('[data-act="close"]').addEventListener('click', () => { ui.proposal = null; emit(); });
  panel.querySelector('[data-act="apply"]').addEventListener('click', async () => {
    const ok = await run(async () => {
      await api.reorderDay(
        store.tripId, store.token, day.day_index, store.state.trip.revision, proposal.proposed_order,
      );
      await syncOnce({ force: true });
    }, '제안 순서를 적용했습니다.');
    if (ok) { ui.proposal = null; emit(); }
  });
  return panel;
}

// ── 초대 패널 ─────────────────────────────────────────────────────────────
function renderInvitePanel(container, state) {
  const trip = state.trip;
  const names = state.participants.map((p) => escapeHtml(p.display_name)).join(' · ');
  container.innerHTML = '<h2>동행 <span>초대코드를 아는 사람은 전체 편집 권한을 갖습니다</span></h2>'
    + `<div class="rowitem"><div><div class="who code">${escapeHtml(trip.invite_code_display)}</div>`
    + `<div class="memo">${names || '아직 나 혼자입니다'}</div></div>`
    + '<button class="mini" type="button" data-act="copy">복사</button></div>';
  container.querySelector('[data-act="copy"]').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(trip.invite_code_display);
      toast('초대코드를 복사했습니다.');
    } catch {
      toast(`초대코드: ${trip.invite_code_display}`);
    }
  });
}

// ── 전체 렌더 ─────────────────────────────────────────────────────────────
function renderAll() {
  if (!store.state) return;
  const state = store.state;
  const day = activeDayState();
  document.documentElement.style.setProperty('--c', day ? day.color : '#22d3ee');

  renderStatusStrip(el('status'), { weather: store.weather, fx: store.fx, syncError: store.syncError });
  renderTripDates(state.days);
  renderProgress(state.progress);
  renderTabs(el('tabs'), state.days, store.activeDay, (dayIndex) => {
    store.activeDay = dayIndex;
    store.sortByDistance = false;
    ui.proposal = null;
    ui.editingSpot = null;
    ui.addingSpot = false;
    emit();
    const target = activeDayState();
    if (tripMap && target) tripMap.focusDay(target);
  });
  renderDaySummary(el('daysum'), day);

  const spotsById = new Map(allSpots().map(({ spot }) => [spot.id, spot]));
  renderAlerts(el('alerts'), {
    warnings: state.warnings, conflicts: state.conflicts, day, spotsById,
  });

  // 근처 장소 (REQ-017). 일정 렌더와 독립이다 — 조회 전에는 아무것도 그리지 않는다.
  renderNearby(el('nearby'), store.nearby, {
    busy: store.nearbyBusy, message: store.nearbyMessage,
  });
  if (tripMap) {
    tripMap.renderNearby((store.nearby && store.nearby.places) || [], {
      onPick: (place) => { store.hint = `${place.name} · ${place.category_label}`; emit(); },
    });
  }
  for (const button of el('nearby').querySelectorAll('[data-add]')) {
    button.addEventListener('click', () => {
      const place = (store.nearby.places || []).find(
        (item) => `${item.osm_type}/${item.osm_id}` === button.dataset.add,
      );
      if (place) addNearbyToItinerary(place);
    });
  }
  el('nearbtn').classList.toggle('on', Boolean(store.nearby || store.nearbyMessage));

  renderCurated(el('curated'), store.curated, {
    busy: store.curatedBusy, message: store.curatedMessage,
  });
  el('curatedbtn').classList.toggle('on', Boolean(store.curated || store.curatedMessage));

  el('sortbtn').classList.toggle('on', store.sortByDistance);
  el('editbtn').classList.toggle('on', store.editMode);
  el('editbtn').setAttribute('aria-pressed', String(store.editMode));
  if (store.hint) el('hint').textContent = store.hint;

  renderCards(el('list'), {
    day,
    days: state.days,
    me: store.me,
    sortByDistance: store.sortByDistance,
    editMode: store.editMode,
    openSpotIds: store.openSpotIds,
    warnings: state.warnings,
    actions,
  });
  renderDayEdit(el('dayedit'), day);

  const participants = state.participants || [];
  renderExpenses(el('expenses'), {
    expenses: store.expenses,
    participants,
    spots: allSpots().map(({ day: d, spot }) => ({ id: spot.id, name: spot.name, day_index: d.day_index })),
    me: participants.find((p) => p.id === ui.meId) || participants[0] || null,
    formOpen: ui.expenseFormOpen,
    actions: {
      openForm() { ui.expenseFormOpen = true; emit(); },
      closeForm() { ui.expenseFormOpen = false; emit(); },
      invalid(message) { toast(message, true); },
      async submit(payload) {
        const ok = await run(async () => {
          await api.createExpense(store.tripId, store.token, payload);
          await Promise.all([syncOnce({ force: true }), refreshLedger()]);
        }, '경비를 기록했습니다.');
        if (ok) { ui.expenseFormOpen = false; emit(); }
      },
      async remove(expenseId) {
        if (!window.confirm('이 경비를 삭제할까요?')) return;
        await run(async () => {
          await api.deleteExpense(store.tripId, store.token, expenseId);
          await Promise.all([syncOnce({ force: true }), refreshLedger()]);
        }, '삭제했습니다.');
      },
    },
  });
  renderSettlement(el('settlement'), store.settlement);
  renderInvitePanel(el('invitePanel'), state);

  // 마커는 상태가 실제로 바뀌었을 때만 다시 찍는다 — 위치가 갱신될 때마다
  // 27개를 지웠다 그리면 지도가 깜빡인다.
  if (tripMap && ui.mapRevision !== state.trip.revision) {
    tripMap.render(state.days);
    ui.mapRevision = state.trip.revision;
  }
}

// ── 시작 ──────────────────────────────────────────────────────────────────
async function boot() {
  const tripId = session.lastTripId();
  const token = tripId ? session.tokenOf(tripId) : null;
  if (!tripId || !token) {
    showGate();
    return;
  }
  store.tripId = tripId;
  store.token = token;
  ui.meId = readLocal(`hl_me_${tripId}`);
  el('gate').hidden = true;
  el('app').hidden = false;

  try {
    await loadEverything();
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      session.forget(tripId);
      showGate('저장된 여행을 찾을 수 없습니다. 새로 만들거나 초대코드로 참여하세요.');
      return;
    }
    showGate(error.message || '여행을 불러오지 못했습니다.');
  }
}

function start() {
  startClock();
  wireGate();
  wireMapBar();
  subscribe(renderAll);
  boot();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', start);
} else {
  start();
}
