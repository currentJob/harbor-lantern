import { escapeHtml as esc, link, formatDistance } from './format.js';
import { haversineMeters } from './geo.js';
import { TripMap } from './map.js';
import { descriptionHtml } from './render/guide.js';
import { reviewHtml } from './render/reviewplan.js';

const wikidata = p => p.wikidata_id || (p.id?.startsWith('wd:') ? p.id.slice(3) : null);
export function samePlace(a, b) {
  return a.id === b.id || Boolean(wikidata(a) && wikidata(a) === wikidata(b))
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

/** One map/list selection flow for researched travel guides and explicit live name searches. */
export function openPlacePicker({plan, dayIndex, apiRequest, onAdd}) {
  const day=plan.days[dayIndex], destination=day.destination || plan.destination;
  const guideCity=day.guide_city || plan.guide_city;
  const dialog=document.createElement('dialog');dialog.className='place-picker';
  dialog.setAttribute('aria-labelledby','pickerTitle');
  dialog.innerHTML=`<div class="picker-heading"><div><p class="eyebrow">${esc(destination.name)} · Day ${dayIndex+1}</p><h2 id="pickerTitle">지도에서 다음 장소 고르기</h2></div><button type="button" class="secondary picker-close" aria-label="장소 선택 닫기">닫기</button></div>
    <form class="picker-search"><label>관광지 이름<input type="search" aria-label="관광지 이름" maxlength="100" placeholder="관광지 이름 · 현지명 · 영문명"></label><button type="submit">실시간 검색</button><button type="button" class="secondary picker-reset">도시 가이드 보기</button><label>참조 자료<select aria-label="참조 자료"><option value="all">전체 자료</option><option value="trip">Trip.com 평점 있는 곳</option><option value="osm">실시간 지도 검색 결과</option></select></label></form>
    <p class="hint picker-help">지도 핀 또는 목록에서 선택해 상세 정보를 확인하세요. Trip.com 평점은 조사 시점 자료이며, 실시간 이름 검색은 도시 중심 50km 이내의 OpenStreetMap 결과입니다.</p>
    <p class="picker-status" role="status">도시 관광지를 불러오는 중입니다.</p>
    <div class="picker-workspace"><div id="placePickerMap" aria-label="추가할 관광지 지도"></div><div class="picker-side"><div class="picker-results" aria-label="관광지 검색 결과"></div><section class="picker-detail" aria-live="polite"><p class="hint">지도 핀을 선택하면 평점·설명·출처를 볼 수 있습니다.</p></section></div></div>`;
  document.body.appendChild(dialog);dialog.showModal();
  const $=selector=>dialog.querySelector(selector), origin=destination;
  const status=$('.picker-status'), results=$('.picker-results'), detail=$('.picker-detail');
  let guides=[], live=[], query='', visible=[], requestId=0, adding=false, mapWarning='';
  const duplicate=place=>day.stops.some(s=>samePlace(s.place,place));
  const map=new TripMap('placePickerMap',{onTileTrouble:text=>{mapWarning=text+' 목록에서 선택할 수 있습니다.';status.textContent=mapWarning;}});
  map.init();
  if(map.map) map.map.setView([origin.lat,origin.lng],12);
  const close=()=>{if(!adding) dialog.close();};
  $('.picker-close').onclick=close;
  dialog.addEventListener('cancel',e=>{if(adding)e.preventDefault();});
  dialog.addEventListener('close',()=>{requestId++;map.map?.remove();map.map=null;dialog.remove();});
  function choose(place, fromMap=false) {
    results.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===place.id)));
    const exists=duplicate(place), full=day.stops.length>=40;
    detail.innerHTML=`<p class="eyebrow">선택한 장소</p><h3>${esc(place.name)}</h3><p class="hint">${esc(place.address || place.name_original || '')}</p>
      ${place.review ? reviewHtml(place.review) : '<p class="hint">확인된 평점·리뷰 수 없음</p>'}
      <details><summary>관광지 설명·영업시간·출처</summary>${descriptionHtml(place)}<p class="hint">영업시간 ${esc(place.hours_text || place.opening_hours || '미확인')}</p>${place.source_url ? link(place.source_url,'지도 자료 원문') : ''}</details>
      <p class="hint">기존 순서를 유지하면서 시간 충돌과 예상 이동거리가 적은 위치에 넣습니다.</p>
      <button type="button" class="picker-add" ${exists || full?'disabled':''}>${exists?'이미 이 날짜에 있음':full?'하루 최대 40곳까지 추가 가능':`Day ${dayIndex+1} 동선에 맞춰 추가`}</button>`;
    if(!fromMap && map.map){
      map.map.setView([place.lat,place.lng],14,{animate:false});
      map.nearbyMarkers[visible.indexOf(place)]?.openPopup();
    }
    detail.querySelector('.picker-add').onclick=async e=>{
      if(adding) return;
      adding=true;e.currentTarget.disabled=true;$('.picker-close').disabled=true;
      try {
        const accepted=await onAdd(place);
        if(accepted) dialog.close();
        else status.textContent='추가하지 못했습니다. 일정의 장소 수와 저장 상태를 확인해 주세요.';
      } catch(error){status.textContent=error.message || '장소를 추가하지 못했습니다. 다시 시도해 주세요.';}
      finally {adding=false;if(dialog.isConnected){$('.picker-close').disabled=false;choose(place,true);}}
    };
  }
  function render() {
    detail.innerHTML='<p class="hint">지도 핀을 선택하면 평점·설명·출처를 볼 수 있습니다.</p>';
    const mode=$('select').value;
    visible=mergePlaces(guides,live,query).filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lng))
      .filter(p=>mode==='trip'?p.review?.source==='Trip.com':mode==='osm'?p.source==='OpenStreetMap':true)
      .map(p=>({...p,distance_m:p.distance_m??haversineMeters(origin,p),category_label:p.review?.source==='Trip.com'?'Trip.com 평점 참고':p.source==='OpenStreetMap'?'OpenStreetMap':'도시 관광 가이드'}));
    results.replaceChildren();
    for(const [i,place] of visible.entries()){
      const button=document.createElement('button');button.type='button';button.dataset.id=place.id;button.setAttribute('aria-pressed','false');
      button.innerHTML=`<span class="picker-number">${i+1}</span><span><strong>${esc(place.name)}</strong><small>${esc(place.category_label)} · ${formatDistance(place.distance_m)}${duplicate(place)?' · 일정에 있음':''}</small>${place.review ? `<small>★ ${esc(place.review.rating)} · ${esc(place.review.review_count)}개 평가</small>`:''}</span>`;
      button.onclick=()=>choose(place);results.appendChild(button);
    }
    map.renderNearby(visible,{onPick:place=>choose(place,true),numbered:true});
    // Keep the same numbered, keyboard-focusable targets in map and list.
    map.nearbyMarkers.forEach((marker,i)=>{
      const icon=marker.getElement();if(icon){icon.setAttribute('aria-label',visible[i].name);icon.title=visible[i].name;}
    });
    map.invalidate();
    if(map.map && visible.length) map.map.fitBounds(visible.map(p=>[p.lat,p.lng]),{padding:[25,25],maxZoom:14,animate:false});
    status.textContent=visible.length?`${visible.length}곳 · 좌표: Wikidata / © OpenStreetMap contributors`:'해당 자료에서 찾은 장소가 없습니다. 전체 자료를 선택하거나 다른 이름으로 실시간 검색해 주세요.';
    if(mapWarning)status.textContent+=' '+mapWarning;
  }
  $('select').onchange=render;
  $('.picker-reset').onclick=()=>{requestId++;live=[];query='';$('input').value='';$('select').value='all';$('[type=submit]').disabled=false;render();};
  $('form').onsubmit=async e=>{
    e.preventDefault();const q=$('input').value.trim();
    if(q.length<2){status.textContent='관광지 이름을 두 글자 이상 입력하세요.';return;}
    const token=++requestId;
    query=q;live=[];render();status.textContent='지도에서 실제 장소를 검색하고 있습니다…';
    const submit=$('[type=submit]');submit.disabled=true;
    try{
      const found=await apiRequest('places/search?'+new URLSearchParams({q,lat:origin.lat,lng:origin.lng}));
      if(!dialog.open || token!==requestId)return;
      live=found.items;render();
    }catch(error){if(dialog.open && token===requestId){render();status.textContent=`${error.message} · 조사된 자료 ${visible.length}곳만 표시합니다. 다시 검색할 수 있습니다.`;}}
    finally{if(token===requestId)submit.disabled=false;}
  };
  if(guideCity?.city_id){
    apiRequest('guides/'+encodeURIComponent(guideCity.city_id)).then(result=>{
      if(!dialog.open)return;guides=result.spots;render();
    }).catch(error=>{if(dialog.open)status.textContent=error.message+' 실시간 이름 검색은 계속 사용할 수 있습니다.';});
  }else {render();status.textContent='조사된 도시 가이드가 없는 지역입니다. 관광지 이름을 실시간 검색해 주세요.';}
}
