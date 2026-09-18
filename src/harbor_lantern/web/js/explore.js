import { apiBase, request, setApiBase } from './api.js';
import { escapeHtml as esc, link } from './format.js';
import { LocationTracker, directionsUrl } from './geo.js';
import { TripMap } from './map.js';
import { cityLabel, daysHtml, gradeBadgeHtml, gradeSummary, isGuidePlan, sourcesHtml, stopDomId } from './render/guide.js';
import { dayPickerLabel, planMapDays, planMapSpotCount, unmappedCount } from './render/planmap.js';
import { reviewHtml, routeHtml, withoutReviews } from './render/reviewplan.js';
import { initPlatform, confidence } from './platform.js';

const $ = (id) => document.getElementById(id);
let destination = null;
// 선택된 **구운 도시**(DSN-46). `destination`(지명 검색 결과)과 따로 둔다 — 둘을 한 변수에
// 담으면 "가이드로 만든 일정"과 "좌표로 만든 일정"을 구분할 수 없게 되고, 그 구분이 AC-085 다.
let city = null;
let activePlan = null;
let foods = [];
let saved = [];
const weekdays = ['월', '화', '수', '목', '금', '토', '일'];
const categories = {restaurant:'음식점', cafe:'카페', fast_food:'간편식', museum:'박물관', gallery:'갤러리', attraction:'명소', viewpoint:'전망', zoo:'동물원', park:'공원'};
try { saved = JSON.parse(localStorage.getItem('hl_explore_plans') || '[]'); if (!Array.isArray(saved)) saved = []; } catch { saved = []; }

function notice(text, error = false) { $('message').textContent = text; $('message').hidden = false; $('message').classList.toggle('error', error); }
async function action(button, work) {
  button.disabled = true;
  try { await work(); } catch (error) { notice(error.message || '요청을 처리하지 못했습니다. 다시 시도해 주세요.', true); }
  finally { button.disabled = false; }
}
const apiRequest = async (path, body) => (await request('/api/explore/' + path, {method:body ? 'POST':'GET',body,timeoutMs:body?.use_reviews ? 120000 : 55000})).data;

/** 구운 도시 목록을 그린다 (DSN-46 · AC-075 · AC-076).
 *
 * **"일본"을 넣으면 여기가 답한다** — 예전에는 지명 검색이 나라를 찾아 "넓은 지역"이라는
 * 막다른 안내로 끝났다. 구운 도시가 하나도 없어도 **오류가 아니다**: 빈 목록과 안내 문구를
 * 그대로 보여 준다(굽기 전의 정상 상태가 그것이다).
 */
function renderGuideCities(result) {
  const cities = (result && result.cities) || [];
  $('guideCityList').replaceChildren();
  $('guideCities').hidden = false;
  const summary = gradeSummary(result && result.counts);
  // 개수는 **등급별로만** 말한다 — "도시 N개 지원" 류의 일괄 주장을 하지 않는다(AC-064).
  $('guideCounts').textContent = summary ? `· 조사된 가이드 ${summary}` : '';
  $('guideCitiesNote').textContent = cities.length
    ? (result.notice || '')
    : '이 검색어로 조사된 도시가 없습니다. 아래 지명 검색에서 목적지를 고르면 제한된 자동 추천을 만듭니다.';
  for (const entry of cities) {
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = cityLabel(entry);
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      city = entry;
      const center = entry.center || {};
      // 근처 맛집 검색은 좌표로 돈다 — 도시를 골랐으면 그 중심을 쓴다(기존 흐름 유지).
      destination = {name: entry.name_ko || entry.city_id, lat: center.lat, lng: center.lng};
      for (const other of $('guideCityList').children) other.setAttribute('aria-pressed', String(other === button));
      for (const other of $('destinations').children) other.setAttribute('aria-pressed', 'false');
      $('selectedDestination').textContent = `${cityLabel(entry)} 선택됨`; $('selectedDestination').hidden = false;
      $('message').hidden = true;
    });
    $('guideCityList').appendChild(button);
  }
}

$('searchDestination').addEventListener('click', (event) => action(event.currentTarget, async () => {
  const query = $('destinationQuery').value.trim();
  if (query.length < 2) return notice('나라와 도시 또는 지역을 두 글자 이상 입력해 주세요.', true);
  destination = null; city = null; $('selectedDestination').hidden = true; $('destinations').replaceChildren();
  notice('여행지를 찾고 있어요…');
  // 구운 도시 목록이 먼저다. 외부 호출이 0건이라 빠르고, 장소 제공자가 죽어도 답한다.
  renderGuideCities(await apiRequest('guides?q=' + encodeURIComponent(query)));
  let result = {items: []};
  let failure = '';
  try { result = await apiRequest('destinations?q=' + encodeURIComponent(query)); }
  catch (error) { failure = error.message || '지명 검색을 마치지 못했어요.'; }
  $('destinationsBlock').hidden = false;
  for (const place of result.items) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = place.name;
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      if (['country','state','province','continent'].includes(place.kind)) {
        notice('넓은 지역을 찾았어요. 하루 동선을 설계할 도시나 동네를 함께 입력해 주세요. 예: 일본 오사카', true);
        return;
      }
      destination = {name:place.name, lat:place.lat, lng:place.lng}; city = null;
      for (const other of $('destinations').children) other.setAttribute('aria-pressed', String(other === button));
      for (const other of $('guideCityList').children) other.setAttribute('aria-pressed', 'false');
      $('selectedDestination').textContent = place.name; $('selectedDestination').hidden = false;
      $('message').hidden = true;
    });
    $('destinations').appendChild(button);
  }
  if (failure) return notice(`조사된 도시 목록만 표시했어요. 지명 검색: ${failure}`, true);
  notice(result.items.length ? '검색 결과에서 여행할 도시·동네를 선택해 주세요.' : '찾는 지역이 없어요. 도시 이름 또는 현지 표기로 다시 검색해 주세요.');
}));
$('destinationQuery').addEventListener('input', () => { destination = null; city = null; $('selectedDestination').hidden = true; });
$('planForm').addEventListener('submit', (event) => {
  event.preventDefault();
  action($('createPlan'), async () => {
    if (!city && !destination) return notice('먼저 여행지를 검색하고 결과에서 선택해 주세요.', true);
    const start = $('startDate').value, end = $('endDate').value;
    const length = (Date.parse(end) - Date.parse(start)) / 86400000;
    if (!Number.isFinite(length) || length < 0 || length > 13) return notice('오는 날은 가는 날 이후이며 여행 기간은 1~14일이어야 합니다.', true);
    notice(city
      ? '조사해 둔 가이드로 날짜별 동선을 만들고 있어요.'
      : '장소와 영업시간을 확인해 날짜별 동선을 설계하고 있어요. 최대 약 40초 걸릴 수 있습니다.');
    // `city_id` 와 `destination` 을 **함께** 보낸다 — 구운 파일이 없으면 서버가 좌표로 폴백해
    // "제한된 자동 추천"을 만든다(REQ-028 · AC-077). 좌표가 없는 항목은 좌표를 빼고 보낸다
    // (없는 값을 0 으로 채워 보내면 서아프리카 앞바다의 일정이 나온다).
    const located = destination && Number.isFinite(destination.lat) && Number.isFinite(destination.lng);
    const result = await apiRequest('plan', {
      ...(city ? {city_id:city.city_id} : {}), ...(located ? {destination} : {}),
      start_date:start,end_date:end,pace:$('pace').value,interests:$('interests').value,radius_m:Number($('planRadius').value),use_reviews:$('planUseReviews').checked,use_ratings:$('planUseRatings').checked});
    activePlan = result;
    let stored = true;
    if (result.scheduled_count) {
      stored = savePlan(result);
    }
    renderPlan(result);
    notice(result.scheduled_count ? `${result.scheduled_count}곳을 연결했어요. ${result.notice}${stored ? '' : ' 저장 공간이 부족해 이번 화면에서만 확인할 수 있어요.'}` : '조건에 맞는 장소를 찾지 못했어요. 주변 범위를 넓히거나 다른 동네를 선택해 주세요.', !result.scheduled_count);
    $('planSection').scrollIntoView({behavior:'smooth',block:'start'});
  });
});

function placeBody(place) {
  const rating = place.rating == null ? '<span class="meta">평점 미제공 · 후기 미제공</span>' : `<span class="rating">★ ${esc(place.rating)} / 5</span> <span class="meta">${esc(place.review_count ?? 0)}개 평가</span>`;
  const menu = place.recommendation || (place.cuisine ? `음식 종류: ${place.cuisine} · 대표 메뉴 미확인` : '대표 메뉴 정보 미제공');
  return `<span class="pill">${esc(categories[place.category] || place.category)}</span><h3>${esc(place.name)}</h3>${place.review ? reviewHtml(place.review) : rating}
    <p class="meta">영업시간: ${esc(place.opening_hours || '미제공 · 방문 전 확인')}</p><p class="meta">${esc(menu)}</p>
    <div class="links">${link(directionsUrl(place.lat,place.lng),'길찾기')}${link(place.menu_url,'메뉴 원문')}${link(place.website,'업체 홈페이지')}${link(place.source_url,'장소 원문')}</div>
    ${(place.reviews || []).map(review => `<div class="review"><div class="review-author">${review.avatar ? `<img src="${esc(review.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}${link(review.author_url,review.author) || esc(review.author)} <span>${esc(review.rating ?? '')}★</span></div><p>${esc(review.text)}</p><small>${esc(review.date)}</small> ${link(review.url,'후기 전체 보기')}</div>`).join('')}
    <p class="source" translate="no">${esc(place.source)}${place.source === 'Google Maps' ? ' · 관련성순 후기' : ''}</p>`;
}
/* ── 일정 지도 (홍콩 화면과 같은 TripMap · 어댑터는 render/planmap.js) ──────────────
 *
 * 지도는 **없어도 되는 부품**이다: Leaflet 이 없거나 타일이 죽어도 목록·타임라인은 그대로다.
 * 위치도 마찬가지다 — 권한을 거부하면 '내 위치' 하나만 빠지고 나머지는 전부 살아 있다.
 * 그래서 권한은 **버튼을 누른 뒤에만** 요청한다(근처 맛집 버튼과 같은 규칙).
 */
let tripMap = null;      // 한 번만 세운다. 실패해도 다시 시도하지 않는다(실패 이유가 안 바뀐다).
let mapBooted = false;
let mapDays = [];
let myPosition = null;
const tracker = new LocationTracker({
  onUpdate: (position) => { myPosition = position; if (tripMap) tripMap.showMe(position); },
  onError: (error) => { tracker.stop(); notice(error.message, true); },
});

function mapNotice(message) { $('planMapMsg').textContent = message; $('planMapMsg').hidden = !message; }

/** 목록의 그 자리로 데려간다. 핀과 카드는 `stopDomId` 라는 같은 문자열로 묶여 있다. */
function focusStopCard(spotId) {
  const card = document.getElementById(spotId);
  if (!card) return;
  for (const other of document.querySelectorAll('.stop.pinned')) other.classList.remove('pinned');
  card.classList.add('pinned');
  card.scrollIntoView({behavior:'smooth', block:'center'});
}

function bootMap() {
  if (mapBooted) return Boolean(tripMap);
  mapBooted = true;
  const map = new TripMap('planMap', {onMarkerClick:(dayIndex, spotId) => {
    window.dispatchEvent(new CustomEvent('hl:pin',{detail:{day:dayIndex}})); focusStopCard(spotId);
  }, onTileTrouble:mapNotice});
  tripMap = map.init() ? map : null;   // 세우지 못했으면 이유는 onTileTrouble 이 이미 적었다
  return Boolean(tripMap);
}

function renderPlanMap(plan) {
  mapDays = planMapDays(plan);
  const pins = planMapSpotCount(mapDays);
  // 일정이 없으면 지도를 띄우지 않는다 — 빈 지도는 "아직 안 그려졌다"로도 읽힌다.
  $('planMapBlock').hidden = !pins;
  $('planMapDay').replaceChildren();
  if (!pins || !bootMap()) return;
  tripMap.render(mapDays);
  for (const day of mapDays) {
    const option = document.createElement('option');
    option.value = String(day.day_index); option.textContent = dayPickerLabel(day);
    $('planMapDay').appendChild(option);
  }
  const first = mapDays.find(day => day.spots.length);
  $('planMapDay').value = String(first.day_index);
  tripMap.invalidate(); tripMap.focusDay(first);
  if (myPosition) tripMap.showMe(myPosition);
  // 좌표가 없어 핀이 안 선 장소가 있으면 **그 사실을 말한다** — 말하지 않으면 지도 고장으로 읽힌다.
  const missing = unmappedCount(plan);
  mapNotice(missing ? `${missing}곳은 좌표가 없어 지도에 표시하지 못했습니다 — 목록에는 그대로 있습니다.` : '');
}

$('planMapDay').addEventListener('change', event => {
  const day = mapDays.find(entry => String(entry.day_index) === event.target.value);
  if (tripMap && day) tripMap.focusDay(day);
});
$('planMapLocate').addEventListener('click', () => {
  if (!tripMap) return notice('지도를 사용할 수 없어 현재 위치를 표시할 수 없습니다. 일정은 그대로 확인할 수 있어요.', true);
  if (myPosition) { tripMap.showMe(myPosition); return tripMap.flyToMe(myPosition); }
  if (!tracker.tracking && !tracker.start()) return;
  notice('현재 위치를 확인하고 있어요…');
});

function renderPlan(plan) {
  window.dispatchEvent(new Event('hl:show-itinerary'));
  activePlan = plan; $('planSection').hidden = false;
  $('planTitle').textContent = plan.destination.name.split(',')[0] + ' 여행';
  $('planMeta').textContent = `${plan.start_date} — ${plan.end_date} · ${plan.scheduled_count}곳 · 이동시간은 추정값`;
  if (plan.review_summary) $('planMeta').textContent += ` · 리뷰 확인 ${plan.review_summary.counts.matched || 0}곳. ${plan.review_summary.notice}`;
  if (plan.rating_summary?.enabled) $('planMeta').textContent += ` · Trip.com 평점·리뷰 수 반영 (${plan.rating_summary.matched}곳 확인)`;
  // 등급 표시는 **두 경로 모두에서 항상** 있다 (AC-085 · AC-077). 폴백 일정도 예외가 아니다.
  $('guideGrade').innerHTML = gradeBadgeHtml(plan);
  const guided = isGuidePlan(plan);
  // 출처·한계는 접어서라도 항상 붙인다 — 한계를 모르면 이 목록을 "그 도시 전부"로 읽는다.
  $('guideSources').innerHTML = guided ? sourcesHtml(plan.guide_city) : '';
  renderPlanMap(plan);
  if (guided) { $('planDays').innerHTML = daysHtml(plan); window.dispatchEvent(new CustomEvent('hl:plan',{detail:{plan,map:tripMap}})); return; }
  $('planDays').innerHTML = plan.days.map((day,index) => `<article class="day"><div class="day-head"><h3>DAY ${index + 1} <span>${esc(day.date)} (${weekdays[day.weekday]})</span></h3><span>${(day.distance_m/1000).toFixed(1)}km · 이동 약 ${day.travel_minutes}분</span></div>${day.stops.length ? day.stops.map((stop,order) => `<div class="stop" id="${stopDomId(index,order)}"><time>${esc(stop.arrival)}<p class="meta">${esc(stop.departure)}</p></time><div><span class="pill ${stop.hours_status === 'unverified' ? 'unknown' : ''}">${stop.hours_status === 'unverified' ? '영업 여부 확인 필요' : '주간 영업시간 반영'}</span>${stop.travel_minutes ? `<span class="pill">이전 장소에서 약 ${stop.travel_minutes}분</span>` : ''}${placeBody(stop.place)}${order ? routeHtml(day.stops[order-1].place, stop.place) : ''}</div></div>`).join('') : '<p class="empty">남은 후보 중 영업시간과 일정에 맞는 장소가 없습니다. 자유시간으로 남겨 두었어요.</p>'}</article>`).join('');
  window.dispatchEvent(new CustomEvent('hl:plan',{detail:{plan,map:tripMap}}));
}
function renderSaved() {
  $('savedSection').hidden = !saved.length; $('savedPlans').replaceChildren();
  saved.forEach(plan => {
    const button = document.createElement('button'); button.textContent = `${plan.destination.name.split(',')[0]} · ${plan.start_date}`;
    button.addEventListener('click', () => {
      destination = plan.destination;
      // 저장된 일정이 가이드로 만든 것이면 도시 선택도 되살린다 — 안 되살리면 같은 화면에서
      // 다시 만들기를 눌렀을 때 조용히 폴백 일정이 나온다.
      city = isGuidePlan(plan) ? {city_id:plan.guide_city.city_id, name_ko:plan.guide_city.name_ko, grade:plan.guide_city.grade, center:{lat:plan.destination.lat, lng:plan.destination.lng}} : null;
      renderPlan(plan); $('planSection').scrollIntoView({behavior:'smooth'});
    });
    $('savedPlans').appendChild(button);
  });
}
function savePlan(plan) {
  plan.local_id ||= `trip-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
  const next = [withoutReviews(plan), ...saved.filter(p => p.local_id !== plan.local_id)].slice(0,10);
  try { localStorage.setItem('hl_explore_plans',JSON.stringify(next)); saved=next; renderSaved(); return true; }
  catch { notice('저장 공간이 부족합니다. 일정 내려받기로 보관해 주세요.',true); return false; }
}
$('downloadPlan').addEventListener('click', () => {
  if (!activePlan) return;
  const blob = new Blob([JSON.stringify(withoutReviews(activePlan),null,2)], {type:'application/json'});
  const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url;
  a.download = `travel-${activePlan.start_date}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
async function findFood(origin) {
  notice('주변 음식점을 조회하고 있어요…');
  const result = await apiRequest(`nearby?lat=${origin.lat}&lng=${origin.lng}&radius_m=${$('nearRadius').value}`);
  foods = result.places; $('foodNotice').textContent = result.notice;
  renderFoods(); notice(foods.length ? `${foods.length}곳을 찾았어요.` : '이 반경 안에서 음식점을 찾지 못했어요. 검색 반경을 넓혀 주세요.');
  window.dispatchEvent(new CustomEvent('hl:food',{detail:{places:foods,origin}}));
}
function renderFoods() {
  const mode=$('foodSort').value;
  const ordered = foods.slice().sort((a,b) => (mode==='confidence' ? confidence(b.rating,b.review_count)-confidence(a.rating,a.review_count) : mode==='rating' ? (b.rating??-1)-(a.rating??-1) : 0) || a.distance_m-b.distance_m);
  $('foodResults').innerHTML = ordered.length ? ordered.map(place => `<article class="place-card" data-food="${esc(place.id)}"><p class="eyebrow">${place.distance_m < 1000 ? place.distance_m + 'm' : (place.distance_m/1000).toFixed(1) + 'km'} 거리</p>${placeBody(place)}</article>`).join('') : '<p class="empty">이 반경에는 검색된 음식점이 없습니다. 반경을 넓혀 다시 찾아보세요.</p>';
}
$('findNearby').addEventListener('click', event => action(event.currentTarget, async () => {
  if (!navigator.geolocation) return notice('이 브라우저는 위치 조회를 지원하지 않습니다. 여행지 기준으로 검색해 주세요.', true);
  const position = await new Promise((resolve,reject) => navigator.geolocation.getCurrentPosition(resolve, error => reject(new Error(error.code === 1 ? '위치 권한이 꺼져 있어요. 브라우저 권한을 허용하거나 여행지 기준으로 검색해 주세요.' : '현재 위치를 확인하지 못했어요. 다시 시도하거나 여행지 기준으로 검색해 주세요.')), {enableHighAccuracy:true,timeout:12000,maximumAge:60000}));
  await findFood({lat:position.coords.latitude,lng:position.coords.longitude});
}));
$('findDestinationFood').addEventListener('click', event => action(event.currentTarget, async () => {
  if (!destination) return notice('여행지를 먼저 검색하고 선택해 주세요.', true);
  await findFood(destination);
}));
$('foodSort').addEventListener('change', renderFoods);
$('backendInput').value = apiBase();
$('backendSave').addEventListener('click', () => { if (setApiBase($('backendInput').value)) location.reload(); else notice('올바른 HTTPS 주소를 입력해 주세요.', true); });
$('policyLinks').innerHTML = link('https://www.google.com/intl/ko/policies/terms/','Google 이용약관') + ' · ' + link('https://www.google.com/intl/ko/policies/privacy/','Google 개인정보처리방침');
const today = new Date();
const dateText = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
$('startDate').value = dateText(today); today.setDate(today.getDate()+3); $('endDate').value = dateText(today);
renderSaved();
initPlatform({apiRequest,getPlan:()=>activePlan,renderPlan,savePlan,notice,action,findFood,
  selectCity:entry=>{
    city=entry;destination={name:entry.name_ko,lat:entry.center.lat,lng:entry.center.lng};
    $('destinationQuery').value=entry.name_ko;
    $('selectedDestination').textContent=cityLabel(entry);$('selectedDestination').hidden=false;
  }});
