import { escapeHtml as esc, link, formatDistance } from './format.js';
import { haversineMeters } from './geo.js';
import { TripMap, ratingLabel } from './map.js';
import { descriptionHtml, hasDescription, tipsHtml } from './render/guide.js';
import { reviewHtml } from './render/reviewplan.js';

const wikidata = p => p.wikidata_id || (p.id?.startsWith('wd:') ? p.id.slice(3) : null);
export function samePlace(a, b) {
  return Boolean(a.id && a.id === b.id) || Boolean(wikidata(a) && wikidata(a) === wikidata(b))
    || (a.name.toLocaleLowerCase() === b.name.toLocaleLowerCase() && haversineMeters(a,b) < 50);
}
const searchable = p => [p.name,p.name_original,p.review?.name,p.address,p.area].filter(Boolean).join(' ').normalize('NFKC').toLocaleLowerCase();
export function mergePlaces(guides, live, query = '') {
  const q = query.trim().normalize('NFKC').toLocaleLowerCase();
  const result = guides.filter(p => !q || searchable(p).includes(q));
  for (const place of live) {
    const guide = guides.find(p => samePlace(p,place));
    const merged = guide ? {...guide,...place,id:guide.id,review:guide.review} : place;
    const index = result.findIndex(p => samePlace(p,merged));
    if (index < 0) result.push(merged); else result[index] = merged;
  }
  return result;
}

const CATEGORY_GROUPS = {
  culture:['museum','gallery','arts_centre'], nature:['park','garden','nature_reserve','beach','peak','waterfall','viewpoint'],
  food:['restaurant','cafe','fast_food'], religion:['place_of_worship','worship'],
  shopping:['marketplace','mall'], history:['castle','ruins','monument','memorial','archaeological_site','historic'],
};
const CATEGORY_LABELS = {all:'전체 종류',attraction:'명소·기타',culture:'박물관·미술관',nature:'자연·전망',history:'유적·기념물',religion:'종교 건축',food:'맛집·카페',shopping:'시장·쇼핑'};
const VISIT_CHECKS = {attraction:'공식 운영시간과 예약 필요 여부를 출발 전에 확인하세요.',culture:'휴관일·마지막 입장 시각과 전시 예약 여부를 확인하세요.',nature:'날씨와 일몰 시각을 확인하고 걷기 편한 신발을 준비하세요.',history:'야외 구간의 계단·경사와 입장 마감 시간을 확인하세요.',religion:'현장 복장 규정과 사진 촬영·참배 예절을 확인하세요.',food:'주문 마감·휴무일과 알레르기 재료를 확인하세요.',shopping:'영업시간과 결제 수단을 확인하고 귀환 이동 시간을 남겨두세요.'};
export const categoryGroup = place => (CATEGORY_LABELS[place.category_group] && place.category_group)
  || Object.keys(CATEGORY_GROUPS).find(k=>CATEGORY_GROUPS[k].includes(place.category)) || 'attraction';
export function withinBounds(place,bounds) {
  return Number.isFinite(place.lat) && Number.isFinite(place.lng) && place.lat>=bounds.south && place.lat<=bounds.north
    && (bounds.west<=bounds.east ? place.lng>=bounds.west && place.lng<=bounds.east : place.lng>=bounds.west || place.lng<=bounds.east);
}
export function filterPlaces(places,filters,origin,bounds) {
  const q=(filters.query || '').trim().normalize('NFKC').toLocaleLowerCase();
  const evidence=p=>p.review || {};
  const rated=p=>Number.isFinite(evidence(p).rating) && evidence(p).rating>=1 && evidence(p).rating<=5 && Number.isInteger(evidence(p).review_count) && evidence(p).review_count>0;
  const result=places.filter(p=>withinBounds(p,bounds))
    .filter(p=>!q || searchable(p).includes(q))
    .filter(p=>!filters.category || filters.category==='all' || categoryGroup(p)===filters.category)
    .filter(p=>!Number(filters.minRating) || (rated(p) && evidence(p).rating>=Number(filters.minRating)))
    .filter(p=>!Number(filters.minReviews) || (rated(p) && evidence(p).review_count>=Number(filters.minReviews)))
    .filter(p=>filters.source==='trip' ? evidence(p).source==='Trip.com' : filters.source==='osm' ? p.source==='OpenStreetMap' : true)
    .map(p=>({...p,distance_m:haversineMeters(origin,p),category_label:CATEGORY_LABELS[categoryGroup(p)]}));
  return result.sort((a,b)=>(filters.sort==='rating' ? (rated(b)?evidence(b).rating:0)-(rated(a)?evidence(a).rating:0)
    : filters.sort==='reviews' ? (rated(b)?evidence(b).review_count:0)-(rated(a)?evidence(a).review_count:0) : 0)
    || a.distance_m-b.distance_m || a.name.localeCompare(b.name,'ko'));
}
function brief(place) {
  return hasDescription(place) ? place.description.slice(0,130)+(place.description.length>130?'…':'')
    : `${CATEGORY_LABELS[categoryGroup(place)]}${place.hours_text?' · 영업시간 '+place.hours_text.slice(0,90):' · 상세 설명 미제공'}`;
}

/** Browse the current viewport, with one debounced request at a time and stale-response rejection. */
export function openPlacePicker({plan, dayIndex, apiRequest, onAdd}) {
  const day=plan.days[dayIndex], destination=day.destination || plan.destination;
  const dialog=document.createElement('dialog');dialog.className='place-picker';
  dialog.setAttribute('aria-labelledby','pickerTitle');
  dialog.innerHTML=`<div class="picker-heading"><div><p class="eyebrow">Day ${dayIndex+1} · 어디서든 찾는 다음 장소</p><h2 id="pickerTitle">지도를 움직이며 관광지 찾기</h2></div><button type="button" class="secondary picker-close" aria-label="장소 선택 닫기">닫기</button></div>
    <form class="picker-search"><label>현재 지도 안에서 이름 검색<input type="search" aria-label="관광지 이름" maxlength="100" placeholder="관광지 이름 · 현지명 · 영문명"></label><button type="submit">이 영역 다시 조회</button><button type="button" class="secondary picker-reset">여행지로</button></form>
    <details class="picker-filter-panel"><summary>종류·별점·리뷰 수 필터와 정렬</summary><div class="picker-filters"><label>종류<select class="filter-category" aria-label="장소 종류">${Object.entries(CATEGORY_LABELS).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
    <label>별점<select class="filter-rating" aria-label="최소 별점"><option value="0">별점 전체</option><option value="1">별점 있는 곳</option><option value="4">4.0 이상</option><option value="4.5">4.5 이상</option></select></label>
    <label>리뷰 수<select class="filter-reviews" aria-label="최소 리뷰 수"><option value="0">리뷰 수 전체</option><option value="100">100개 이상</option><option value="1000">1,000개 이상</option></select></label>
    <label>자료<select class="filter-source" aria-label="참조 자료"><option value="all">전체 자료</option><option value="trip">Trip.com 평점 있는 곳</option><option value="osm">OpenStreetMap</option></select></label>
    <label>정렬<select class="filter-sort" aria-label="장소 정렬"><option value="distance">지도 중심 가까운 순</option><option value="rating">별점 높은 순</option><option value="reviews">리뷰 많은 순</option></select></label><button type="button" class="secondary filter-reset">필터 초기화</button></div></details>
    <p class="hint picker-help">국가·도시 제한 없이 지도를 이동하면 보이는 영역을 자동 조회합니다. 별점·리뷰 수는 확인된 Trip.com 조사 자료이며 없는 곳은 ‘평가 정보 없음’으로 표시합니다. 한 영역의 공개 지도 장소 최대 500건과 조사 자료를 함께 표시하며, 등록되지 않은 장소는 빠질 수 있습니다.</p>
    <p class="picker-status" role="status" aria-live="polite">지도 영역을 확인하고 있습니다.</p>
    <div class="picker-workspace"><div id="placePickerMap" aria-label="추가할 관광지 지도"></div><div class="picker-side"><div class="picker-results" aria-label="관광지 검색 결과"></div><section class="picker-detail" aria-live="polite"><p class="hint">장소를 고르면 상세 정보와 방문 팁을 볼 수 있습니다.</p></section></div></div>`;
  document.body.appendChild(dialog);dialog.showModal();
  const narrow=window.matchMedia('(max-width:760px)');
  const adjustFilters=()=>{dialog.querySelector('.picker-filter-panel').open=!narrow.matches;};
  adjustFilters();narrow.addEventListener('change',adjustFilters);
  const $=selector=>dialog.querySelector(selector), status=$('.picker-status'), results=$('.picker-results'), detail=$('.picker-detail');
  let items=[],visible=[],selected=null,generation=0,busy=false,pending=null,timer=null,adding=false,mapWarning='',message='',lastKey='';
  let bounds={south:destination.lat-.025,west:destination.lng-.025,north:destination.lat+.025,east:destination.lng+.025};
  const duplicate=place=>day.stops.some(s=>samePlace(s.place,place));
  const map=new TripMap('placePickerMap',{onTileTrouble:text=>{mapWarning=text+' 목록에서 선택할 수 있습니다.';status.textContent=mapWarning;}});
  map.init();
  if(map.map){map.map.setView([destination.lat,destination.lng],15);L.control.zoom({position:'topright'}).addTo(map.map);}
  const center=()=>({lat:(bounds.south+bounds.north)/2,lng:((bounds.west+(bounds.east-bounds.west+360)%360/2+540)%360)-180});
  function readBounds() {
    if(!map.map)return bounds;
    const b=map.map.getBounds(),wrap=v=>((v+180)%360+360)%360-180;
    return {south:Math.max(-90,b.getSouth()),west:wrap(b.getWest()),north:Math.min(90,b.getNorth()),east:wrap(b.getEast())};
  }
  const close=()=>{if(!adding)dialog.close();};
  $('.picker-close').onclick=close;
  dialog.addEventListener('cancel',e=>{if(adding)e.preventDefault();});
  dialog.addEventListener('close',()=>{generation++;clearTimeout(timer);pending=null;narrow.removeEventListener('change',adjustFilters);map.map?.remove();map.map=null;dialog.remove();});
  function choose(place) {
    selected=place.id;
    results.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===selected)));
    const exists=duplicate(place),full=day.stops.length>=40;
    detail.innerHTML=`<p class="eyebrow">${esc(place.category_label)} · 선택한 장소</p><h3>${esc(place.name)}</h3><p class="hint">${esc(place.address || place.name_original || '')}</p>
      ${place.review?reviewHtml(place.review):'<p class="hint">평가 정보 없음 · 공개 지도는 별점·리뷰를 제공하지 않습니다.</p>'}
      ${descriptionHtml(place)}<p class="hint">영업시간 ${esc(place.hours_text || place.opening_hours || '미확인')}</p>
      <details open><summary>방문 전 알아두기</summary>${tipsHtml(place)}<p class="hint">일반 방문 체크 · ${esc(VISIT_CHECKS[categoryGroup(place)])}</p></details>
      <p class="source">${place.source_url?link(place.source_url,'지도 자료 원문'):''} ${place.website?link(place.website,'공식 웹사이트'):''}</p>
      <p class="hint">기존 순서를 유지하며 시간 충돌과 예상 이동거리가 적은 위치에 넣습니다.${haversineMeters(destination,place)>50000?' 여행지에서 50km 이상 떨어져 있습니다. 도시 간 교통편과 이동시간은 별도로 확인하세요.':''}</p>
      <button type="button" class="picker-add" ${exists||full?'disabled':''}>${exists?'이미 이 날짜에 있음':full?'하루 최대 40곳까지 추가 가능':`Day ${dayIndex+1} 동선에 맞춰 추가`}</button>`;
    detail.querySelector('.picker-add').onclick=async e=>{
      if(adding)return;
      adding=true;e.currentTarget.disabled=true;$('.picker-close').disabled=true;
      try{if(await onAdd(place))dialog.close();else status.textContent='추가하지 못했습니다. 일정과 저장 상태를 확인해 주세요.';}
      catch(error){status.textContent=error.message || '장소를 추가하지 못했습니다. 다시 시도해 주세요.';}
      finally{adding=false;if(dialog.isConnected){$('.picker-close').disabled=false;choose(place);}}
    };
  }
  function render() {
    const filters={query:$('input').value,category:$('.filter-category').value,minRating:$('.filter-rating').value,
      minReviews:$('.filter-reviews').value,source:$('.filter-source').value,sort:$('.filter-sort').value};
    visible=filterPlaces(items,filters,center(),bounds);
    results.replaceChildren();
    for(const [i,place] of visible.entries()){
      const button=document.createElement('button');button.type='button';button.dataset.id=place.id;button.setAttribute('aria-pressed',String(place.id===selected));
      const tip=(place.tips || []).find(t=>t.text && t.evidence);
      button.innerHTML=`<span class="picker-number">${i+1}</span><span><strong>${esc(place.name)}</strong><small>${esc(place.category_label)} · 중심에서 ${formatDistance(place.distance_m)}${duplicate(place)?' · 일정에 있음':''}</small><small class="picker-list-rating">${esc(ratingLabel(place))}</small><small>${esc(brief(place))}</small><small>${tip?'방문 팁 · '+esc(tip.text.slice(0,120)):'일반 방문 체크 · '+esc(VISIT_CHECKS[categoryGroup(place)])}</small></span>`;
      button.onclick=()=>{choose(place);map.nearbyMarkers[i]?.openPopup();};results.appendChild(button);
    }
    if(!visible.length)results.innerHTML='<p class="empty">현재 영역에서 조건에 맞는 장소가 없습니다. 필터를 초기화하거나 지도를 이동·확대해 보세요.</p>';
    map.renderNearby(visible,{onPick:choose,numbered:true});
    map.nearbyMarkers.forEach((marker,i)=>{const icon=marker.getElement();if(icon){icon.setAttribute('aria-label',visible[i].name);icon.title=visible[i].name;}});
    const kept=visible.find(p=>p.id===selected);
    if(kept&&!adding)choose(kept);
    else if(!adding){selected=null;detail.innerHTML='<p class="hint">장소를 고르면 상세 정보와 방문 팁을 볼 수 있습니다.</p>';}
    status.textContent=`${visible.length}곳 표시 / 영역 자료 ${items.filter(p=>withinBounds(p,bounds)).length}곳 · ${message}${mapWarning?' '+mapWarning:''}`;
  }
  async function pump() {
    if(busy || !pending || !dialog.open)return;
    const job=pending;pending=null;busy=true;
    try{
      const found=await apiRequest('places/viewport?'+new URLSearchParams(job.bounds));
      if(!dialog.open || job.generation!==generation)return;
      items=found.items;lastKey=found.partial?'':job.key;
      message=(found.truncated?'장소가 많아 일부만 조회했습니다. 지도를 더 확대해 주세요. ':'')+found.notice;
      render();
    }catch(error){if(dialog.open && job.generation===generation){message=`조회 실패: ${error.message} ‘이 영역 다시 조회’를 눌러주세요.`;render();}}
    finally{busy=false;if(pending&&dialog.open){clearTimeout(timer);timer=setTimeout(pump,700);}}
  }
  function queue(force=false) {
    if(!dialog.open || adding)return;
    bounds=readBounds();
    const width=(bounds.east-bounds.west+360)%360, height=bounds.north-bounds.south;
    const area=height*width*111**2*Math.cos(center().lat*Math.PI/180);
    generation++;pending=null;clearTimeout(timer);
    if((map.map && map.map.getZoom()<12) || width>1 || height>1 || area>1200){
      message='지도를 더 확대하면 이 영역의 장소를 조회합니다. 국가·지역 제한은 없습니다.';render();return;
    }
    const requestBounds={...Object.fromEntries(Object.entries(bounds).map(([k,v])=>[k,Number(v.toFixed(5))])),category:$('.filter-category').value};
    const key=JSON.stringify(requestBounds);
    if(!force && key===lastKey){message='현재 영역의 조회 자료입니다. 별점·리뷰 수는 조사 시점 기준입니다.';render();return;}
    pending={bounds:requestBounds,key,generation};message='이 영역의 장소를 조회하고 있습니다…';render();timer=setTimeout(pump,700);
  }
  $('.picker-search').onsubmit=e=>{e.preventDefault();queue(true);};
  $('input').oninput=render;
  $('.picker-filters').onchange=e=>{if(e.target.matches('.filter-category'))queue(true);else render();};
  $('.filter-reset').onclick=()=>{$('input').value='';$('.picker-filters').querySelectorAll('select').forEach(s=>s.selectedIndex=0);queue(true);};
  $('.picker-reset').onclick=()=>{if(map.map)map.map.setView([destination.lat,destination.lng],15);else queue(true);};
  map.map?.on('moveend',()=>queue());
  queue();
}
