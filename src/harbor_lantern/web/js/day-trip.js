import { escapeHtml as esc, link } from './format.js';

export function canAddMacau(plan) {
  return plan.start_date === '2026-10-05' && plan.end_date === '2026-10-08'
    && (plan.guide_city?.city_id === 'hong-kong' || /홍콩|hong kong/i.test(plan.destination?.name || ''));
}

export function withMacauDay(plan, template, date) {
  if (!canAddMacau(plan) || plan.days.some(d => d.excursion?.id === 'macau-2026')) throw new Error('이미 마카오 일정이 있거나 대상 여행이 아닙니다.');
  const next = structuredClone(plan), index = next.days.findIndex(d => d.date === date);
  if (index < 0) throw new Error('여행에 포함된 날짜를 선택해 주세요.');
  const day = structuredClone(template.days[0]);
  day.date = date; day.weekday = (new Date(date+'T12:00:00Z').getUTCDay()+6)%7;
  day.excursion.original_day = next.days[index];
  next.days[index] = day;
  return recount(next);
}

export function restoreMacauDay(plan) {
  const next = structuredClone(plan);
  next.days = next.days.map(d => d.excursion?.original_day || d);
  return recount(next);
}

function recount(plan) {
  const places = plan.days.flatMap(d => d.stops.map(s => s.place));
  plan.scheduled_count = places.length;
  delete plan.review_summary;
  plan.rating_summary = {enabled:true,matched:places.filter(p=>p.review?.source==='Trip.com').length};
  return plan;
}

export function excursionHtml(day) {
  const info = day.excursion;
  if (!info) return '';
  return `<aside class="excursion-notes"><p class="eyebrow">HONG KONG → MACAO · 당일치기</p><h3>${esc(info.title)}</h3><p>${esc(info.reason)}</p>
    <ol>${info.transport.map(t=>`<li>${esc(t)}</li>`).join('')}</ol>
    <p class="hint">${esc(info.fare)}</p><p class="hint">${esc(info.caution)}</p>
    <details><summary>출발 전 준비물과 현지 팁</summary><ul>${info.tips.map(t=>`<li>${esc(t)}</li>`).join('')}</ul></details>
    <p class="source">확인 ${esc(info.checked_at)} · ${info.sources.map(s=>link(s.url,s.name)).join(' · ')}</p></aside>`;
}

/** Explicit, reversible application to browser-local trips; never silently migrates a user's plans. */
export function renderMacauOffer({root,plans,savePlan,renderPlan,action,notice}) {
  const matches = plans.map((plan,index)=>({plan,index})).filter(({plan})=>canAddMacau(plan));
  root.innerHTML = `<section class="section macau-offer"><div><p class="eyebrow">ONE DAY ACROSS THE WATER</p><h2>홍콩 여행에, 마카오 하루</h2><p>10월 5–8일 여행 중 하루를 외항 터미널에서 시작하는 구시가지 코스로 바꿔 보세요. 왕복 페리 안내와 준비물도 함께 담았습니다.</p></div>
    <p>외항 → 성바울 유적 → 성 도밍고 성당 → 세나도 광장·점심 → 아마 사원 → 외항</p>
    <div class="tools"><label>마카오 방문일<select class="macau-date">${[5,6,7,8].map(d=>`<option value="2026-10-0${d}" ${d===7?'selected':''}>10월 ${d}일${d===7?' (추천)':''}</option>`).join('')}</select></label>
    ${matches.length?`<label>적용할 여행<select class="macau-target">${matches.map(({plan,index},i)=>`<option value="${index}">${esc(plan.destination.name)} · 10/5–8${matches.length>1?` · 여행 ${i+1}`:''}</option>`).join('')}</select></label>`:''}
    <button class="macau-preview secondary">코스 먼저 보기</button><button class="macau-apply">${matches.length?'선택한 하루를 마카오로 변경':'마카오 하루 일정 저장'}</button><button class="macau-restore secondary" hidden>변경 전 하루 복원</button></div>
    <p class="hint">${matches.length?'나머지 날짜는 그대로 두고, 변경 전 하루는 여행 안에 보관합니다.':'이 브라우저에는 10/5–8 홍콩 여행이 없습니다. 기존 여행을 저장한 브라우저에서 열면 그 여행에 적용할 수 있습니다. 여기서는 마카오 하루만 저장합니다.'}</p></section>`;
  const date = root.querySelector('.macau-date'), target = root.querySelector('.macau-target');
  const apply = root.querySelector('.macau-apply'), restore = root.querySelector('.macau-restore');
  const selected = () => target ? plans[Number(target.value)] : null;
  function update() {
    const existing=selected()?.days.find(d=>d.excursion?.id==='macau-2026');
    apply.disabled=Boolean(existing); restore.hidden=!existing?.excursion.original_day;
    date.disabled=Boolean(existing); if(existing) date.value=existing.date;
  }
  if(target) target.onchange=update;
  update();
  async function template() {
    const response=await fetch('./data/macau-day-trip.json');
    if(!response.ok) throw new Error('마카오 코스를 불러오지 못했습니다. 다시 시도해 주세요.');
    const plan=await response.json();
    plan.start_date=plan.end_date=plan.days[0].date=date.value;
    plan.days[0].weekday=(new Date(date.value+'T12:00:00Z').getUTCDay()+6)%7;
    return plan;
  }
  root.querySelector('.macau-preview').onclick=e=>action(e.currentTarget,async()=>renderPlan(await template()));
  apply.onclick=e=>action(e.currentTarget,async()=>{
    const plan=await template(), original=selected();
    const next=original?withMacauDay(original,plan,date.value):plan;
    if(!savePlan(next)) return;
    renderPlan(next);
    const day=next.days.findIndex(d=>d.excursion?.id==='macau-2026');
    document.querySelector(`#itineraryTabs [data-day="${day}"]`)?.click();
    notice('마카오 일정과 이동 안내를 내 여행에 저장했습니다. 페리 승선권은 별도로 예약해 주세요.');
  });
  restore.onclick=e=>action(e.currentTarget,async()=>{
    const next=restoreMacauDay(selected());
    if(savePlan(next)){renderPlan(next);notice('마카오로 바꾸기 전 하루를 복원했습니다.');}
  });
}
