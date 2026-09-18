import { escapeHtml as esc, link } from '../format.js';

const STATUS = {
  not_requested: '후기 조회 전', disabled: '후기 연결이 설정되지 않았습니다',
  unmatched: '장소 일치 확인 실패 · 후기 미제공', unavailable: '후기 조회 실패 · 다시 시도하세요',
  busy: '후기 조회 혼잡 · 다시 시도하세요', limit: '이번 조회 범위 초과 · 후기 미제공',
};

export function withoutReviews(plan) {
  const copy = JSON.parse(JSON.stringify(plan));
  delete copy.review_summary;
  for (const day of copy.days || []) {
    for (const stop of day.stops || []) {
      if (stop.place.review?.source !== 'Trip.com') delete stop.place.review;
    }
  }
  return copy;
}

export function reviewHtml(review) {
  if (!review || review.status !== 'matched') return `<p class="memo">${esc(STATUS[review?.status] || '후기 미제공')}</p>`;
  const snapshot = review.source === 'Trip.com';
  return `<div class="review-evidence"><p><b translate="no">${snapshot ? 'Trip.com' : 'Google Maps'}</b> · ${
    review.rating == null ? '평점 미제공' : `★ ${esc(review.rating)} / 5`
  } · ${esc(review.review_count ?? '미제공')}개 평가 ${link(review.source_url, '장소 원문')}</p>
  <p class="memo">조회 ${esc((review.fetched_at || '').slice(0,10))} · ${snapshot ? '평점·리뷰 수 스냅샷' : '관련성순 후기 일부'}</p>
  ${(review.reviews || []).map(r => `<blockquote><p>${esc(r.text)}</p><small>${
    link(r.author_url, r.author) || esc(r.author)
  } · ${esc(r.rating ?? '미제공')}★ · ${esc(r.date)} ${link(r.url, '후기 원문')}</small></blockquote>`).join('')}
  ${review.reviews?.length ? '' : '<p class="memo">후기 본문 미제공</p>'}</div>`;
}

export function routeHtml(origin, destination) {
  if (![origin?.lat, origin?.lng, destination?.lat, destination?.lng].every(Number.isFinite)) return '';
  const base = 'https://www.google.com/maps/dir/?' + new URLSearchParams({
    api: '1', origin: `${origin.lat},${origin.lng}`, destination: `${destination.lat},${destination.lng}`,
  });
  return `<div class="route-links"><span>${esc(origin.name)} → ${esc(destination.name)}</span> ${
    link(base + '&travelmode=walking', '도보 길찾기')
  } ${link(base + '&travelmode=transit', '대중교통 길찾기')}</div>`;
}

export function proposalHtml(result, revision) {
  const stale = result.expected_revision !== revision;
  return `<p class="memo">${esc(result.notice)}</p><p class="memo">${esc(result.review_summary.notice)}</p>
    <p>리뷰 확인 ${result.review_summary.counts.matched || 0}곳 / ${Object.values(result.review_summary.counts).reduce((a,b) => a+b,0)}곳</p>
    ${!result.reviews_enabled ? '<p class="memo">리뷰 제공자 연결이 없어 일정·영업시간·거리로 점검했습니다. 후기 원문은 각 장소의 지도에서 확인할 수 있습니다.</p>' : ''}
    ${stale ? '<p role="status">일정이 변경되었습니다. 다시 점검한 뒤 적용하세요.</p>' : ''}
    ${result.days.map(day => {
      const before = day.current.day.totals, after = day.proposed.day.totals;
      const issues = [...day.proposed.warnings.map(w => {
        const spot = day.proposed.day.spots.find(s => s.id === w.spot_id);
        return `${spot?.name || ''}: ${w.message}`;
      }), ...day.proposed.conflicts.map(c => {
        const spot = day.proposed.day.spots.find(s => s.id === c.spot_id);
        return `${spot?.name || ''}: 고정시각과 ${c.overlap_minutes}분 겹칩니다.`;
      }), ...day.advice];
      return `<article class="review-day"><h3>DAY ${day.day_index} · ${esc(day.title)}</h3><p>${esc(day.date)} · ${day.proposed.day.spots.length}곳</p>
        <p>이동 예상 ${before.travel_minutes}분 → ${after.travel_minutes}분 · 직선거리 ${(before.distance_m/1000).toFixed(1)} → ${(after.distance_m/1000).toFixed(1)}km</p>
        <p>${esc(day.reason)}</p>${issues.length ? `<ul>${issues.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : '<p class="memo">확인 가능한 정보에서 충돌을 찾지 못했습니다.</p>'}
        <details><summary>현재 순서와 제안 일정·후기·루트 보기</summary>
        <p class="memo">현재: ${day.current.day.spots.map(s => esc(s.name)).join(' → ')}</p>
        <ol>${day.proposed.day.spots.map((s, i, spots) => `<li><b>${esc(s.schedule.eta_local)}${s.schedule.eta_day_offset ? ` (+${s.schedule.eta_day_offset}일)` : ''} · ${esc(s.name)}</b>
          <p class="memo">체류 ${s.dwell_minutes}분${s.review_priority == null ? '' : ` · 평가 수 보정 점수 ${s.review_priority}/5`}</p>
          ${reviewHtml(s.review)}${i ? routeHtml(spots[i-1], s) : link(s.directions_url, '첫 장소로 길찾기')}</li>`).join('')}</ol></details>
        <button class="primary" type="button" data-apply-day="${day.day_index}" ${stale || !day.improved ? 'disabled' : ''}>Day ${day.day_index} 제안 적용</button></article>`;
    }).join('')}`;
}
