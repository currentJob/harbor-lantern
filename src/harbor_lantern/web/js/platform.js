import { escapeHtml as esc, link } from './format.js';
import { spotBody, gradeSummary } from './render/guide.js';
import { TripMap } from './map.js';
import { openPlacePicker } from './place-picker.js';
import { excursionHtml } from './day-trip.js';

const $ = id => document.getElementById(id);
const EUROPE = new Set(['NL','ES','TR','GB','FR','CZ','IT','AT']);
const OTHER = new Set(['US','AU']);
export function confidence(rating, count) {
  return Number.isFinite(rating) && rating >= 1 && rating <= 5 && Number.isInteger(count) && count > 0
    ? (rating * count + 3.5 * 50) / (count + 50) : -1;
}

export function initPlatform({apiRequest, selectCity, getPlan, renderPlan, savePlan, notice, action, findFood}) {
  let cities = [], detail = null, selectedDay = 0, map = null, foodMap = null, sequence = 0, editing = false;
  const show = view => {
    document.querySelectorAll('[data-page]').forEach(el => { el.hidden = el.dataset.page !== view; });
    document.querySelectorAll('[data-view]').forEach(el => {
      const active = el.dataset.view === (view === 'city' ? 'discover' : view === 'itinerary' ? 'planner' : view);
      if (active) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
    });
    $('savedEmpty').hidden = Boolean($('savedPlans').children.length);
    if (view === 'itinerary') requestAnimationFrame(() => map?.invalidate());
    if (view === 'food') requestAnimationFrame(() => foodMap?.invalidate());
  };
  const go = view => { show(view); if (location.hash !== '#'+view) location.hash = view; };
  function route() {
    const hash = location.hash.slice(1) || 'discover';
    window.scrollTo(0,0);
    if (hash === 'itinerary' && !getPlan()) { show('saved'); return; }
    if (hash.startsWith('city/')) { show('city'); openCity(hash.slice(5)); }
    else if (hash === 'cities') show('discover');
    else show(['discover','planner','food','saved','itinerary'].includes(hash) ? hash : 'discover');
  }
  window.addEventListener('hashchange', route);
  window.addEventListener('hl:show-itinerary', () => go('itinerary'));
  function catalogue() {
    const q = $('citySearch').value.trim().toLocaleLowerCase();
    const region = $('cityRegion').value;
    const ordered = cities.filter(c => [c.name_ko,c.name_en,c.country_ko,c.country_en].join(' ').toLocaleLowerCase().includes(q))
      .filter(c => region === 'all' || (EUROPE.has(c.country_code) ? 'europe' : OTHER.has(c.country_code) ? 'other' : 'asia') === region);
    const featured = ['hong-kong','tokyo','prague','paris','singapore','osaka','london','rome'];
    ordered.sort((a,b) => $('citySort').value === 'name' ? a.name_ko.localeCompare(b.name_ko,'ko')
      : $('citySort').value === 'ratings' ? b.rated_count-a.rated_count
      : (featured.includes(a.city_id) ? featured.indexOf(a.city_id) : 99) - (featured.includes(b.city_id) ? featured.indexOf(b.city_id) : 99));
    $('cityCards').innerHTML = ordered.length ? ordered.map(c => `<article class="city-card"><a class="city-image" href="#city/${esc(c.city_id)}" aria-label="${esc(c.name_ko)} 가이드 보기"><img src="./assets/cities/${esc(c.city_id)}.jpg" alt="${esc(c.photo?.title || c.name_ko)}" width="400" height="300" loading="lazy"></a><h3><a href="#city/${esc(c.city_id)}">${esc(c.name_ko)}</a></h3><p class="country">${esc(c.country_ko)} · ${esc(c.name_en)}</p><p class="city-facts">${c.grade === 'full' ? '완전 가이드' : '부분 가이드'} · ${c.spot_count}곳<br><strong>평점 확인 ${c.rated_count || 0}곳</strong></p>${c.photo ? `<span class="photo-credit">사진 · ${link(c.photo.source_url, 'Trip.com / '+c.photo.title)}</span>` : ''}</article>`).join('') : '<p class="empty">조건에 맞는 도시가 없습니다. 다른 도시나 나라 이름으로 찾아보세요.</p>';
  }
  for (const id of ['citySearch','cityRegion','citySort']) $(id).addEventListener(id === 'citySearch' ? 'input' : 'change', catalogue);
  async function openCity(id) {
    const token = ++sequence;
    $('cityDetail').innerHTML = '<p class="empty" role="status">도시 가이드를 펼치는 중입니다.</p>';
    try {
      const result = await apiRequest('guides/'+encodeURIComponent(id));
      if (token !== sequence) return;
      detail = result;
      const entry = cities.find(c => c.city_id === id) || result;
      selectCity(entry);
      $('cityDetail').innerHTML = `<a href="#discover">← 모든 도시</a><section class="city-cover"><div><p class="country">${esc(result.country_ko)} · ${esc(result.name_en)}</p><h1>${esc(result.name_ko)}</h1><p class="city-description">조사된 ${result.spot_count}곳에서 하루의 동선을 고릅니다.<br>지도, 날짜별 일정, 가까운 한 끼까지 한곳에서.</p><p class="hint">${esc(result.notice)}</p><p class="hint">Trip.com 평점 확인 ${entry.rated_count || 0}곳 · 평가 수를 보정해 추천에 반영</p><div class="city-actions"><button id="cityPreview">추천 일정 바로 보기</button><button class="secondary" id="cityConfigure">날짜 정해서 계획하기</button></div></div><figure><img src="./assets/cities/${esc(id)}.jpg" alt="${esc(entry.photo?.title || result.name_ko)}" width="600" height="400">${entry.photo ? `<figcaption class="hint">${link(entry.photo.source_url, '사진 · Trip.com / '+entry.photo.title)}</figcaption>` : ''}</figure></section><div class="city-toolbar"><h2>이 도시에서 만날 장소</h2><label>장소 정렬<select id="spotSort"><option value="confidence">평점·리뷰 수 함께</option><option value="original">가이드 기본 순</option></select></label><button class="secondary" id="cityFood">이 도시 주변 맛집</button></div><p class="hint">평점 없는 장소도 가이드에 포함됩니다. 평점은 조사 시점 자료이며 도시 전체의 순위를 뜻하지 않습니다.</p><div id="cityPlaces" class="city-place-grid"></div>`;
      renderPlaces();
      $('spotSort').addEventListener('change',renderPlaces);
      $('cityConfigure').onclick = () => go('planner');
      $('cityFood').onclick = () => { $('foodCity').value = id; go('food'); action($('findDestinationFood'), () => findFood(result.center)); };
      $('cityPreview').onclick = event => action(event.currentTarget, async () => {
        const start = $('startDate').value, end = new Date(start+'T12:00:00'); end.setDate(end.getDate()+2);
        const date = `${end.getFullYear()}-${String(end.getMonth()+1).padStart(2,'0')}-${String(end.getDate()).padStart(2,'0')}`;
        const plan = await apiRequest('plan',{city_id:id,start_date:start,end_date:date,use_ratings:true});
        savePlan(plan); selectedDay = 0; renderPlan(plan); window.scrollTo(0,0);
      });
    } catch(error) {
      if (token !== sequence) return;
      $('cityDetail').innerHTML = `<h1>가이드를 연결하지 못했습니다</h1><p>${esc(error.message)}</p><button id="retryCity">다시 시도</button><p><a href="#discover">도시 목록으로 돌아가기</a></p>`;
      $('retryCity').onclick = () => openCity(id);
      notice('하단 연결 설정에서 서버 주소를 확인해 주세요.',true);
    }
  }
  function renderPlaces() {
    const places = detail.spots.slice();
    if ($('spotSort').value === 'confidence') places.sort((a,b) => confidence(b.review?.rating,b.review?.review_count)-confidence(a.review?.rating,a.review?.review_count));
    $('cityPlaces').innerHTML = places.map(p => `<article class="catalogue-place">${spotBody(p)}${!p.review ? '<p class="hint">평점·리뷰 수 미확인</p>' : ''}</article>`).join('');
  }
  function activateDay(index) {
    const plan = getPlan(); if (!plan) return;
    selectedDay = Math.min(Math.max(0,index),plan.days.length-1);
    document.querySelectorAll('#planDays > .day').forEach((el,i) => {el.hidden = i !== selectedDay;});
    document.querySelectorAll('#itineraryTabs button').forEach((el,i) => el.setAttribute('aria-selected',String(i === selectedDay)));
    $('planMapDay').value = String(selectedDay);
    $('planMapDay').dispatchEvent(new Event('change'));
  }
  window.addEventListener('hl:plan', event => {
    const {plan} = event.detail; map = event.detail.map;
    $('itineraryTabs').innerHTML = plan.days.map((d,i) => `<button type="button" role="tab" data-day="${i}" aria-selected="false">Day ${i+1} <small>${esc(d.date.slice(5))}</small></button>`).join('');
    $('itineraryTabs').setAttribute('role','tablist');
    document.querySelectorAll('#planDays .day').forEach((dayEl,di) => {
      dayEl.querySelector('.day-head').insertAdjacentHTML('afterend',excursionHtml(plan.days[di]));
      dayEl.querySelectorAll('.stop').forEach((el,si) => {
        const stop = plan.days[di].stops[si];
        el.classList.toggle('completed',Boolean(stop.completed));
        const controls = document.createElement('div'); controls.className='stop-controls';
        controls.innerHTML = `<button class="visit-toggle" aria-pressed="${Boolean(stop.completed)}" data-edit="check">${stop.completed ? '방문 완료' : '방문 체크'}</button><button data-edit="up" aria-label="${esc(stop.place.name)} 위로" ${si===0?'disabled':''}>↑</button><button data-edit="down" aria-label="${esc(stop.place.name)} 아래로" ${si===plan.days[di].stops.length-1?'disabled':''}>↓</button><label>날짜<select data-edit="move" aria-label="${esc(stop.place.name)} 방문 날짜">${plan.days.map((d,i)=>`<option value="${i}" ${i===di?'selected':''}>Day ${i+1}</option>`).join('')}</select></label><button data-edit="remove" aria-label="${esc(stop.place.name)} 일정에서 제외">제외</button>`;
        for (const warning of stop.warnings || []) { const p=document.createElement('p');p.className='edit-warning';p.textContent=warning;el.lastElementChild.appendChild(p); }
        el.lastElementChild.appendChild(controls);
        controls.addEventListener('click',e => { const btn=e.target.closest('button[data-edit]');if(btn) action(btn,()=>edit(di,si,btn.dataset.edit)); });
        controls.querySelector('select').onchange=e=>action(e.target,()=>edit(di,si,'move',Number(e.target.value)));
        const duration = document.createElement('label');
        duration.innerHTML = '체류(분) <input type="number" min="5" max="720" step="5" aria-label="체류시간(분)">';
        const field = duration.querySelector('input');
        const minutes = time => time.split(':').reduce((h,m) => Number(h)*60+Number(m));
        field.value = stop.duration || Math.max(5,minutes(stop.departure)-minutes(stop.arrival));
        field.onchange = () => {
          if (!field.reportValidity()) return;
          action(field,()=>edit(di,si,'duration',Number(field.value)));
        };
        controls.appendChild(duration);
        const time = document.createElement('label');
        time.innerHTML='방문 시각 <input type="time" aria-label="방문 시각">';
        time.querySelector('input').value=stop.fixed_start || '';
        time.querySelector('input').onchange=e=>action(e.target,()=>edit(di,si,'time',e.target.value || null));
        controls.appendChild(time);
        const auto=document.createElement('button');auto.textContent='시각 자동';auto.disabled=!stop.fixed_start;
        auto.onclick=()=>action(auto,()=>edit(di,si,'time',null));controls.appendChild(auto);
      });
      const start=document.createElement('label');start.className='day-start';
      start.innerHTML='하루 시작 <input type="time" aria-label="하루 시작 시각" required>';
      start.querySelector('input').value=plan.days[di].start_time || '09:00';
      start.querySelector('input').onchange=e=>{if(e.target.reportValidity())action(e.target,()=>edit(di,0,'start',e.target.value));};
      dayEl.querySelector('.day-head').after(start);
      {
        const add = document.createElement('button'); add.className='secondary add-place';
        add.textContent='지도에서 관광지 찾아 추가'; dayEl.appendChild(add);
        add.onclick=()=>openPlacePicker({plan,dayIndex:di,apiRequest,onAdd:place=>edit(di,0,'add',place)});
      }
    });
    activateDay(selectedDay);
  });
  $('itineraryTabs').onclick = e => {const button=e.target.closest('[data-day]');if(button) activateDay(Number(button.dataset.day));};
  // Map pins select the matching day before bringing the corresponding stop into view.
  window.addEventListener('hl:pin',e=>activateDay(e.detail.day));
  $('planMapDay').addEventListener('change',e=>{
    const day=Number(e.target.value);if(day!==selectedDay) activateDay(day);
  });
  async function edit(di,si,kind,target) {
    if(editing) return notice('앞선 일정 변경을 저장하고 있습니다. 잠시 후 다시 시도해 주세요.');
    editing=true;
    try {
    const plan = structuredClone(getPlan()), stops = plan.days[di].stops;
    let insertedIndex=null;
    if(kind==='check') stops[si].completed=!stops[si].completed;
    else {
      if(kind==='up' && si>0) [stops[si-1],stops[si]]=[stops[si],stops[si-1]];
      if(kind==='down' && si<stops.length-1) [stops[si+1],stops[si]]=[stops[si],stops[si+1]];
      if(kind==='remove') stops.splice(si,1);
      if(kind==='duration') stops[si].duration=target;
      if(kind==='time') stops[si].fixed_start=target;
      if(kind==='start') plan.days[di].start_time=target;
      if(kind==='add') {
        if(stops.length>=40) return notice('하루 최대 40곳까지 담을 수 있습니다.',true);
      }
      if(kind==='move') {plan.days[target].stops.push(stops.splice(si,1)[0]);selectedDay=target;}
      const minutes=t=>{const [h,m]=t.split(':').map(Number);return h*60+m;};
      const days=plan.days.map(d=>({date:d.date,start_time:d.start_time||'09:00',title:d.title||'',area:d.area||'',color:d.color||'#245548',stops:d.stops.map(s=>({place:s.place,duration:s.duration||Math.max(5,minutes(s.departure)-minutes(s.arrival)),completed:Boolean(s.completed),fixed_start:s.fixed_start||null}))}));
      const result=await apiRequest('recalculate',{days,...(kind==='add'?{insert:{day_index:di,stop:{place:target,duration:90}}}:{})});
      plan.days=result.days.map((day,index)=>index===di || (kind==='move' && index===target)
        ? {...plan.days[index],...day} : plan.days[index]);plan.scheduled_count=result.scheduled_count;
      insertedIndex=result.insertion?.stop_index ?? null;
      if(kind==='add') selectedDay=di;
    }
    const stored=savePlan(plan);renderPlan(plan);
    if(insertedIndex!==null) {
      const id=`plan-d${di}s${insertedIndex}`;
      document.getElementById(id)?.scrollIntoView({block:'center',behavior:'smooth'});
      map?.focus({...target,id});
    }
    if(stored) notice(kind==='add' ? '시간 충돌과 예상 이동거리를 비교해 추가하고 저장했습니다. 기존 장소 순서는 유지합니다.' : '변경한 일정을 내 여행에 저장했습니다. 이동시간은 추정값입니다.');
    return true;
    } finally { editing=false; }
  }
  window.addEventListener('hl:food',event=>{
    const {places,origin}=event.detail;
    if(!foodMap){foodMap=new TripMap('foodMap',{onTileTrouble:text=>notice(text,true)});foodMap.init();}
    foodMap.renderNearby(places,{onPick:place=>document.querySelector(`[data-food="${CSS.escape(String(place.id))}"]`)?.scrollIntoView({block:'center',behavior:'smooth'})});
    foodMap.invalidate();
    if(foodMap.map) foodMap.map.setView([origin.lat,origin.lng],15);
  });
  $('foodCity').onchange=()=>{const c=cities.find(c=>c.city_id===$('foodCity').value);if(c) selectCity(c);};
  fetch('./data/cities.json').then(r=>{if(!r.ok)throw new Error('도시 목록을 불러오지 못했습니다.');return r.json();}).then(data=>{
    cities=data.cities;
    $('catalogueMeta').textContent=`조사된 가이드 ${gradeSummary(data.counts)} · 지도와 날짜별 추천 일정`;
    $('foodCity').innerHTML='<option value="">도시 선택</option>'+cities.map(c=>`<option value="${esc(c.city_id)}">${esc(c.name_ko)} · ${esc(c.country_ko)}</option>`).join('');
    catalogue();route();
  }).catch(error=>{notice(error.message,true);$('cityCards').innerHTML='<p class="empty">도시 목록을 불러오지 못했습니다. 페이지를 새로고침해 주세요.</p>';});
  route();
  return {go};
}
