import { apiBase, request, setApiBase } from './api.js';
import { escapeHtml as esc } from './format.js';
import { directionsUrl } from './geo.js';

const $ = (id) => document.getElementById(id);
let destination = null;
let activePlan = null;
let foods = [];
let saved = [];
const weekdays = ['월', '화', '수', '목', '금', '토', '일'];
const categories = {restaurant:'음식점', cafe:'카페', fast_food:'간편식', museum:'박물관', gallery:'갤러리', attraction:'명소', viewpoint:'전망', zoo:'동물원', park:'공원'};
try { saved = JSON.parse(localStorage.getItem('hl_explore_plans') || '[]'); if (!Array.isArray(saved)) saved = []; } catch { saved = []; }

function notice(text, error = false) { $('message').textContent = text; $('message').hidden = false; $('message').classList.toggle('error', error); }
function link(url, label) {
  try { const parsed = new URL(url); if (!['https:', 'http:'].includes(parsed.protocol)) return ''; }
  catch { return ''; }
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;
}
async function action(button, work) {
  button.disabled = true;
  try { await work(); } catch (error) { notice(error.message || '요청을 처리하지 못했습니다. 다시 시도해 주세요.', true); }
  finally { button.disabled = false; }
}
const apiRequest = async (path, body) => (await request('/api/explore/' + path, {method:body ? 'POST':'GET',body,timeoutMs:55000})).data;

$('searchDestination').addEventListener('click', (event) => action(event.currentTarget, async () => {
  const query = $('destinationQuery').value.trim();
  if (query.length < 2) return notice('나라와 도시 또는 지역을 두 글자 이상 입력해 주세요.', true);
  destination = null; $('selectedDestination').hidden = true; $('destinations').replaceChildren();
  notice('여행지를 찾고 있어요…');
  const result = await apiRequest('destinations?q=' + encodeURIComponent(query));
  for (const place of result.items) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = place.name;
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      if (['country','state','province','continent'].includes(place.kind)) {
        notice('넓은 지역을 찾았어요. 하루 동선을 설계할 도시나 동네를 함께 입력해 주세요. 예: 일본 오사카', true);
        return;
      }
      destination = {name:place.name, lat:place.lat, lng:place.lng};
      for (const other of $('destinations').children) other.setAttribute('aria-pressed', String(other === button));
      $('selectedDestination').textContent = place.name; $('selectedDestination').hidden = false;
      $('message').hidden = true;
    });
    $('destinations').appendChild(button);
  }
  notice(result.items.length ? '검색 결과에서 여행할 도시·동네를 선택해 주세요.' : '찾는 지역이 없어요. 도시 이름 또는 현지 표기로 다시 검색해 주세요.');
}));
$('destinationQuery').addEventListener('input', () => { destination = null; $('selectedDestination').hidden = true; });
$('planForm').addEventListener('submit', (event) => {
  event.preventDefault();
  action($('createPlan'), async () => {
    if (!destination) return notice('먼저 여행지를 검색하고 결과에서 선택해 주세요.', true);
    const start = $('startDate').value, end = $('endDate').value;
    const length = (Date.parse(end) - Date.parse(start)) / 86400000;
    if (!Number.isFinite(length) || length < 0 || length > 13) return notice('오는 날은 가는 날 이후이며 여행 기간은 1~14일이어야 합니다.', true);
    notice('장소와 영업시간을 확인해 날짜별 동선을 설계하고 있어요. 최대 약 40초 걸릴 수 있습니다.');
    const result = await apiRequest('plan', {destination,start_date:start,end_date:end,pace:$('pace').value,interests:$('interests').value,radius_m:Number($('planRadius').value)});
    activePlan = result;
    let stored = true;
    if (result.scheduled_count) {
      saved = [result, ...saved].slice(0,10);
      try { localStorage.setItem('hl_explore_plans', JSON.stringify(saved)); } catch { stored = false; }
      renderSaved();
    }
    renderPlan(result);
    notice(result.scheduled_count ? `${result.scheduled_count}곳을 연결했어요. ${result.notice}${stored ? '' : ' 저장 공간이 부족해 이번 화면에서만 확인할 수 있어요.'}` : '조건에 맞는 장소를 찾지 못했어요. 주변 범위를 넓히거나 다른 동네를 선택해 주세요.', !result.scheduled_count);
    $('planSection').scrollIntoView({behavior:'smooth',block:'start'});
  });
});

function placeBody(place) {
  const rating = place.rating == null ? '<span class="meta">평점 미제공 · 후기 미제공</span>' : `<span class="rating">★ ${esc(place.rating)} / 5</span> <span class="meta">${esc(place.review_count ?? 0)}개 평가</span>`;
  const menu = place.recommendation || (place.cuisine ? `음식 종류: ${place.cuisine} · 대표 메뉴 미확인` : '대표 메뉴 정보 미제공');
  return `<span class="pill">${esc(categories[place.category] || place.category)}</span><h3>${esc(place.name)}</h3>${rating}
    <p class="meta">영업시간: ${esc(place.opening_hours || '미제공 · 방문 전 확인')}</p><p class="meta">${esc(menu)}</p>
    <div class="links">${link(directionsUrl(place.lat,place.lng),'길찾기')}${link(place.menu_url,'메뉴 원문')}${link(place.website,'업체 홈페이지')}${link(place.source_url,'장소 원문')}</div>
    ${(place.reviews || []).map(review => `<div class="review"><div class="review-author">${review.avatar ? `<img src="${esc(review.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}${link(review.author_url,review.author) || esc(review.author)} <span>${esc(review.rating ?? '')}★</span></div><p>${esc(review.text)}</p><small>${esc(review.date)}</small> ${link(review.url,'후기 전체 보기')}</div>`).join('')}
    <p class="source" translate="no">${esc(place.source)}${place.source === 'Google Maps' ? ' · 관련성순 후기' : ''}</p>`;
}
function renderPlan(plan) {
  activePlan = plan; $('planSection').hidden = false;
  $('planTitle').textContent = plan.destination.name.split(',')[0] + ' 여행';
  $('planMeta').textContent = `${plan.start_date} — ${plan.end_date} · ${plan.scheduled_count}곳 · 이동시간은 추정값`;
  $('planDays').innerHTML = plan.days.map((day,index) => `<article class="day"><div class="day-head"><h3>DAY ${index + 1} <span>${esc(day.date)} (${weekdays[day.weekday]})</span></h3><span>${(day.distance_m/1000).toFixed(1)}km · 이동 약 ${day.travel_minutes}분</span></div>${day.stops.length ? day.stops.map(stop => `<div class="stop"><time>${esc(stop.arrival)}<p class="meta">${esc(stop.departure)}</p></time><div><span class="pill ${stop.hours_status === 'unverified' ? 'unknown' : ''}">${stop.hours_status === 'unverified' ? '영업 여부 확인 필요' : '주간 영업시간 반영'}</span>${stop.travel_minutes ? `<span class="pill">이전 장소에서 약 ${stop.travel_minutes}분</span>` : ''}${placeBody(stop.place)}</div></div>`).join('') : '<p class="empty">남은 후보 중 영업시간과 일정에 맞는 장소가 없습니다. 자유시간으로 남겨 두었어요.</p>'}</article>`).join('');
}
function renderSaved() {
  $('savedSection').hidden = !saved.length; $('savedPlans').replaceChildren();
  saved.forEach(plan => {
    const button = document.createElement('button'); button.textContent = `${plan.destination.name.split(',')[0]} · ${plan.start_date}`;
    button.addEventListener('click', () => { destination = plan.destination; renderPlan(plan); $('planSection').scrollIntoView({behavior:'smooth'}); });
    $('savedPlans').appendChild(button);
  });
}
$('downloadPlan').addEventListener('click', () => {
  if (!activePlan) return;
  const blob = new Blob([JSON.stringify(activePlan,null,2)], {type:'application/json'});
  const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url;
  a.download = `travel-${activePlan.start_date}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
async function findFood(origin) {
  notice('주변 음식점을 조회하고 있어요…');
  const result = await apiRequest(`nearby?lat=${origin.lat}&lng=${origin.lng}&radius_m=${$('nearRadius').value}`);
  foods = result.places; $('foodNotice').textContent = result.notice;
  renderFoods(); notice(foods.length ? `${foods.length}곳을 찾았어요.` : '이 반경 안에서 음식점을 찾지 못했어요. 검색 반경을 넓혀 주세요.');
}
function renderFoods() {
  const ordered = foods.slice().sort($('foodSort').value === 'rating' ? (a,b) => (b.rating ?? -1)-(a.rating ?? -1) || a.distance_m-b.distance_m : (a,b) => a.distance_m-b.distance_m);
  $('foodResults').innerHTML = ordered.map(place => `<article class="place-card"><p class="eyebrow">${place.distance_m < 1000 ? place.distance_m + 'm' : (place.distance_m/1000).toFixed(1) + 'km'} AWAY</p>${placeBody(place)}</article>`).join('');
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
