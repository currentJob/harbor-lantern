/* 구운 도시 가이드 렌더 — DSN-46 (REQ-024 · REQ-028 · REQ-029 · AC-069 · AC-070 · AC-072 · AC-077 · AC-080 · AC-085)
 *
 * 여기 있는 것은 **문자열을 돌려주는 순수 함수**다. DOM 을 만지는 곳은 `explore.js` 하나이고,
 * 그래서 이 파일은 Node 로 그대로 실행해 검증한다(`tests/static/test_guide_frontend.py` —
 * `format.js`·`geo.js` 에 쓴 방식과 같다).
 *
 * 이 화면이 말해야 하는 것은 세 가지이고, 셋 다 **없을 때가 정상인 데이터**다.
 *
 * 1. **지금 보는 일정이 어느 수준인가**(AC-085). 완전·부분·제한된 자동 추천 — 세 문구가
 *    서로 다르다. 같은 문구를 돌려쓰면 등급을 실어 보내는 의미가 없다.
 * 2. **이 설명을 누가 썼는가**(AC-070 · AC-080). 설명에는 반드시 출처 링크와 라이선스가 붙는다.
 *    출처 세 필드(url·license·retrieved_at) 중 하나라도 없으면 **설명을 그리지 않는다** —
 *    출처 없는 문장은 우리가 쓴 문장처럼 보이고, 그것이 CC BY-SA 4.0 의 저작자 표시를 깬다.
 * 3. **없는 것은 없다고**(AC-072). 빈자리를 권유형 상투구로 메우지 않는다(금칙 문구 목록은
 *    `tests/static/test_guide_frontend.py` 가 센다). 빈칸으로 두지도 않는다 — 빈칸은
 *    "아직 로딩 중"으로도 읽히고 "그런 것이 없는 장소"로도 읽힌다.
 */

import { escapeHtml as esc, link } from '../format.js';

/** 등급은 이 셋뿐이다. 서버(`api/routes/explore.py`)의 `GRADE_NOTICE` 키와 같다. */
export const GRADES = ['full', 'partial', 'heuristic'];

/** 배지에 쓰는 짧은 이름. 목록·일정 머리에 항상 붙는다(AC-085). */
export const GRADE_LABEL = {
  full: '완전 가이드',
  partial: '부분 가이드',
  heuristic: '제한된 자동 추천',
};

/** 한 줄 안내. **셋이 서로 다르다** — 사용자가 화면만 보고 수준을 구분할 수 있어야 한다. */
export const GRADE_NOTICE = {
  full: '완전 가이드 · 조사 시점 {retrieved_at} — 주요 명소와 한국어 설명을 확인한 도시입니다.',
  partial: '부분 가이드 — 자료가 적어 일정 일부가 채워지지 않을 수 있습니다. 조사 시점 {retrieved_at}',
  heuristic: '제한된 자동 추천 — 미리 조사한 가이드가 없는 지역입니다. 영업시간·중요도 근거가 약합니다.',
};

/** 구운 도시의 출처 패널에 **고정으로** 들어가는 한 줄 (설계서 §16.1.1 · REQ-029).
 *  사람이 고르고 쓴 홍콩 가이드와 성격이 다르다는 사실을 화면이 직접 말한다. */
export const MACHINE_MADE_NOTE =
  '이 일정은 공개 데이터에서 자동으로 구성했습니다. 사람이 고르고 쓴 홍콩 가이드와는 선별·설명의 성격이 다릅니다.';

export const NO_DESCRIPTION = '설명 미제공';
export const NOT_PROVIDED = '미제공';

const CATEGORY_LABEL = {
  attraction: '명소', museum: '박물관', gallery: '갤러리', worship: '종교 시설',
  castle: '성·궁전', park: '공원', monument: '기념물', building_landmark: '건축물',
  bridge: '다리', river: '강', tower: '타워·전망', viewpoint: '전망',
};

const WEEKDAYS = ['월', '화', '수', '목', '금', '토', '일'];

const text = (value) => String(value == null ? '' : value).trim();

/** 일정 응답의 등급 (AC-085).
 *
 * **모르는 값은 `heuristic` 으로 읽는다.** 서버는 구운 도시의 이상한 등급을 `partial` 로
 * 낮춰 읽지만(그쪽 입력은 이미 '구운 도시'다), 화면에 오는 일정에는 등급이 아예 없는 것이
 * 있다 — 이 기능 전에 브라우저에 저장된 일정들이다. 그것을 "부분 가이드"라고 부르면
 * 조사한 적 없는 일정에 조사했다는 표시를 붙이는 것이 된다. 올려 읽지 않는다.
 */
export function planGrade(plan) {
  const value = text(plan && plan.guide_grade);
  return GRADES.includes(value) ? value : 'heuristic';
}

/** 구운 도시 목록 항목의 등급. 목록에는 `full`·`partial` 만 실린다(미달 도시는 없다). */
export function cityGrade(city) {
  const value = text(city && city.grade);
  return value === 'full' ? 'full' : 'partial';
}

/** 등급 한 줄. `retrieved_at` 이 없으면 날짜 자리도 **비우지 않고** 미제공이라고 쓴다. */
export function gradeNotice(grade, retrievedAt) {
  const key = GRADES.includes(grade) ? grade : 'heuristic';
  return GRADE_NOTICE[key].replace('{retrieved_at}', text(retrievedAt) || NOT_PROVIDED);
}

/** 화면에 띄울 안내 — 서버가 준 문구가 있으면 그것을 쓴다(두 곳의 문구가 갈리지 않게). */
export function planNotice(plan) {
  const served = text(plan && plan.guide_notice);
  const source = (plan && plan.guide_city) || (plan && plan.guide_source) || {};
  return served || gradeNotice(planGrade(plan), source.retrieved_at);
}

/** 도시 개수는 **등급별로만** 말한다 — "도시 N개 지원" 류의 일괄 주장을 하지 않는다(AC-064). */
export function gradeSummary(counts) {
  const source = counts || {};
  const parts = [];
  if (Number(source.full) > 0) parts.push(`완전 ${Number(source.full)}`);
  if (Number(source.partial) > 0) parts.push(`부분 ${Number(source.partial)}`);
  return parts.join(' · ');
}

/** 목록 버튼에 쓰는 한 줄. 등급이 이름 옆에 항상 붙는다. */
export function cityLabel(city) {
  const source = city || {};
  const parts = [text(source.name_ko) || text(source.name_en) || text(source.city_id)];
  const where = text(source.country_ko) || text(source.country_en);
  if (where) parts.push(where);
  parts.push(GRADE_LABEL[cityGrade(source)]);
  if (Number(source.spot_count) > 0) parts.push(`${Number(source.spot_count)}곳`);
  return parts.join(' · ');
}

/** 설명을 그려도 되는가 — **출처 세 필드가 모두 있고 연결 검증이 실패하지 않은 경우만**(AC-070). */
export function hasDescription(spot) {
  const source = spot || {};
  const origin = source.description_source || {};
  const verified = source.verification || {};
  return Boolean(
    text(source.description)
    && text(origin.url)
    && text(origin.license)
    && text(origin.retrieved_at)
    && text(verified.status) !== 'failed',
  );
}

/** 출처 한 줄: `출처: {문서 제목} ↗ · CC BY-SA 4.0 ↗ · 조회 2026-09-15` (AC-080).
 *  라이선스에도 링크를 건다 — CC BY-SA 는 라이선스 링크를 요구한다. */
export function sourceHtml(origin) {
  const source = origin || {};
  const label = text(source.title) || text(source.url);
  const anchor = link(source.url, label);
  if (!anchor) return '';
  const license = link(source.license_url, text(source.license)) || esc(text(source.license));
  const parts = [anchor];
  if (text(source.license)) parts.push(license);
  if (text(source.retrieved_at)) parts.push(`조회 ${esc(source.retrieved_at)}`);
  return `<p class="source">출처: ${parts.join(' · ')}</p>`;
}

/** 설명 + 출처. 없으면 **"설명 미제공"** 이다 — 빈칸으로 두지 않는다(AC-070). */
export function descriptionHtml(spot) {
  if (!hasDescription(spot)) return `<p class="missing">${NO_DESCRIPTION}</p>`;
  return `<p class="desc">${esc(spot.description)}</p>${sourceHtml(spot.description_source)}`;
}

/** 팁 — 근거(`evidence`)가 있는 항목만 그린다. 하나도 없으면 "미제공"(AC-072). */
export function tipsHtml(spot) {
  const rows = ((spot || {}).tips || []).filter((tip) => tip && text(tip.text) && text(tip.evidence));
  if (!rows.length) return `<p class="missing">방문 팁 ${NOT_PROVIDED}</p>`;
  return `<ul class="tips">${rows.map((tip) => {
    const anchor = link(tip.source_url, '근거');
    return `<li>${esc(tip.text)}${anchor ? ` <span class="source">${anchor}</span>` : ''}</li>`;
  }).join('')}</ul>`;
}

/** 추천 — 원천이 없으면 빈 배열이고 화면은 "미제공"이다. 문장을 지어내지 않는다(AC-072). */
export function recommendationsHtml(spot) {
  const rows = ((spot || {}).recommendations || []).filter((row) => row && text(row.text || row));
  if (!rows.length) return `<p class="missing">추천 ${NOT_PROVIDED}</p>`;
  return `<ul class="tips">${rows.map((row) => {
    const body = text(row.text || row);
    const anchor = link(row.source_url, '근거');
    return `<li>${esc(body)}${anchor ? ` <span class="source">${anchor}</span>` : ''}</li>`;
  }).join('')}</ul>`;
}

/** 영업시간 — OSM 원문과 그 출처. 없으면 "미제공 · 방문 전 확인"(폴백 화면과 같은 문구). */
export function hoursHtml(spot) {
  const source = spot || {};
  const hours = text(source.hours_text);
  if (!hours) return `<p class="meta">영업시간: ${NOT_PROVIDED} · 방문 전 확인</p>`;
  const origin = source.hours_source || {};
  const anchor = link(origin.url, text(origin.provider) || '영업시간 원문');
  return `<p class="meta">영업시간: ${esc(hours)}${anchor ? ` <span class="source">${anchor}</span>` : ''}</p>`;
}

/** 스팟 하나. 이름(AC-069) → 설명·출처(AC-070) → 영업시간 → 팁·추천(AC-072) 순이다. */
export function spotBody(spot) {
  const source = spot || {};
  const category = text(source.category);
  const area = text(source.area);
  const original = text(source.name_original);
  const pills = [category ? `<span class="pill">${esc(CATEGORY_LABEL[category] || category)}</span>` : '',
    area ? `<span class="pill">${esc(area)}</span>` : ''].join('');
  const name = text(source.name) || text(source.name_ko);
  return `${pills}<h3>${esc(name)}${original ? ` <span class="orig" translate="no">${esc(original)}</span>` : ''}</h3>
    ${descriptionHtml(source)}${hoursHtml(source)}${tipsHtml(source)}${recommendationsHtml(source)}`;
}

/** `#rrggbb` 만 통과. 색은 데이터에서 온다 — 검사 없이 style 에 넣으면 그것이 주입 경로다. */
export function safeColor(value) {
  return /^#[0-9a-fA-F]{3,8}$/.test(text(value)) ? text(value) : '';
}

/** 일정 한 줄의 DOM 식별자 — **지도 핀과 목록을 잇는 유일한 끈**이다.
 *
 * 장소 자체의 id 를 쓰지 않는다: 같은 장소가 다른 날에 또 나올 수 있고, 폴백 일정의
 * 장소에는 id 가 아예 없다. 위치(몇째 날 · 몇째 순서)는 두 경로 모두에 항상 있다.
 * 지도 어댑터(`render/planmap.js`)와 이 렌더러가 **같은 함수**를 불러야 둘이 갈라지지 않는다.
 */
export function stopDomId(dayIndex, stopIndex) {
  return `plan-d${Number(dayIndex) || 0}s${Number(stopIndex) || 0}`;
}

/** 하루 카드. 테마(`title`·`area`·`color`)는 홍콩 가이드의 일자 탭과 같은 시각 언어다. */
export function dayHtml(day, index) {
  const source = day || {};
  const color = safeColor(source.color);
  const theme = [text(source.title), text(source.area)].filter(Boolean).join(' · ');
  const stops = (source.stops || []);
  const exception = source.area_exception
    ? `<p class="dayexception">이 날은 다른 지역의 대표 장소가 포함되어 있습니다. ${esc(text(source.area_exception_reason))}</p>`
    : '';
  const body = stops.length
    ? stops.map((stop, order) => `<div class="stop" id="${stopDomId(index, order)}"><time>${esc(text(stop.arrival))}<p class="meta">${esc(text(stop.departure))}</p></time><div>${
      stop.evening_slot ? '<span class="pill">저녁 배치 · 19:00 기준 추정</span>' : ''
    }<span class="pill ${stop.hours_status === 'unverified' ? 'unknown' : ''}">${
      stop.hours_status === 'unverified' ? '영업 여부 확인 필요' : '주간 영업시간 반영'
    }</span>${stop.travel_minutes ? `<span class="pill">이전 장소에서 약 ${esc(stop.travel_minutes)}분</span>` : ''}${
      spotBody(stop.place)
    }</div></div>`).join('')
    : '<p class="empty">이 날에 배정할 수 있는 조사된 장소가 없습니다. 일정이 채워지지 않았습니다.</p>';
  return `<article class="day"><div class="day-head"${color ? ` style="border-left:6px solid ${color}"` : ''}>
    <h3>DAY ${index + 1} <span>${esc(text(source.date))} (${WEEKDAYS[source.weekday] || ''})</span>${
      theme ? `<span class="daytheme">${esc(theme)}</span>` : ''
    }</h3>
    <span>${((Number(source.distance_m) || 0) / 1000).toFixed(1)}km · 이동 약 ${Number(source.travel_minutes) || 0}분</span>
    </div>${exception}${body}</article>`;
}

/** 일정 전체(날짜 카드들). */
export function daysHtml(plan) {
  return ((plan && plan.days) || []).map((day, index) => dayHtml(day, index)).join('');
}

/** 출처·한계 패널. **접어서라도 항상 붙인다** — 한계를 모르고 보면 이 목록을 "그 도시 전부"로
 *  읽는다(미쉐린 목록 `curatedgaps` 와 같은 규칙 · 같은 마크업). */
export function sourcesHtml(guideCity) {
  const source = guideCity || {};
  const gaps = (source.known_gaps || []).map((gap) => `<li>${esc(gap)}</li>`).join('');
  const links = (source.sources || [])
    .map((row) => link(row && row.url, text(row && row.what) || text(row && row.url)))
    .filter(Boolean).join(' · ');
  const retrieved = text(source.retrieved_at);
  return `<details class="guidegaps"><summary>이 일정에 대해 · 출처와 한계</summary>
    <p>${MACHINE_MADE_NOTE}</p>
    ${retrieved ? `<p class="meta">조사 시점 ${esc(retrieved)}</p>` : ''}
    ${gaps ? `<ul>${gaps}</ul>` : `<p class="missing">알려진 한계 ${NOT_PROVIDED}</p>`}
    ${links ? `<p class="source">${links}</p>` : ''}</details>`;
}

/** 이 일정이 구운 가이드로 만든 것인가. 폴백 일정과 렌더러를 가르는 유일한 분기다. */
export function isGuidePlan(plan) {
  return Boolean(plan && plan.guide_city && text(plan.guide_city.city_id));
}

/** 등급 배지 한 줄 — 일정이 있는 화면에는 **항상** 있다(AC-085 · AC-077). */
export function gradeBadgeHtml(plan) {
  const grade = planGrade(plan);
  return `<span class="gradebadge ${esc(grade)}">${GRADE_LABEL[grade]}</span> <span class="gradenote">${esc(planNotice(plan))}</span>`;
}
