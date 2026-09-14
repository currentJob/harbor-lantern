/* 위치 추적 · 거리 (설계서 §6.15 · AC-032 · AC-033 · AC-034 · 리스크 R5)
 *
 * 원본 정적 페이지의 watchPosition / haversine / fmt / 가까운 순 정렬을 그대로 옮긴다.
 * 거리 계산은 **클라이언트가** 한다 — 현재 위치는 서버가 알 필요도 없고 알아서도 안 된다.
 * 서버의 domain/geo.py 와 같은 공식·같은 표기 규칙을 쓴다(AC-032 를 양쪽에서 검증한다).
 */

import { formatDistance } from './format.js';

/** 지구 반경(m). 서버 domain/geo.py 와 같은 값이어야 한다. */
export const EARTH_RADIUS_M = 6371000;

export function haversineMeters(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLng = (b.lng - a.lng) * rad;
  const x = Math.sin(dLat / 2) ** 2
    + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(x));
}

/** 원본의 fmt(). 표기 규칙 자체는 format.js 에 한 벌만 둔다. */
export const fmt = formatDistance;

/** 구글맵 길찾기 딥링크 (AC-034). 서버도 같은 문자열을 spot.directions_url 로 준다. */
export function directionsUrl(lat, lng) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
}

/** 가까운 순 정렬 (AC-033).
 *  위치가 없으면 **정렬 요청을 무시하고 원래 순서를 그대로** 돌려준다 — 복사본을 만들어
 *  돌려주므로 호출자의 배열은 건드리지 않는다. */
export function sortByDistance(items, me, coordOf) {
  if (!me) return items.slice();
  return items
    .map((item, index) => ({ item, index, d: haversineMeters(me, coordOf(item)) }))
    .sort((a, b) => (a.d - b.d) || (a.index - b.index))
    .map((entry) => entry.item);
}

/** 실시간 위치 추적. 원본과 같은 옵션(고정밀·5초 캐시·15초 타임아웃). */
export class LocationTracker {
  constructor({ onUpdate, onError }) {
    this.onUpdate = onUpdate;
    this.onError = onError;
    this.watchId = null;
    this.position = null;
  }

  get supported() {
    return typeof navigator !== 'undefined' && 'geolocation' in navigator;
  }

  get tracking() {
    return this.watchId != null;
  }

  start() {
    if (!this.supported) {
      this.onError({ code: 'unsupported', message: '이 브라우저는 위치 기능을 지원하지 않습니다.' });
      return false;
    }
    if (this.watchId != null) return true;
    this.watchId = navigator.geolocation.watchPosition(
      (p) => {
        this.position = { lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy };
        this.onUpdate(this.position);
      },
      (error) => {
        this.onError({
          code: error.code === 1 ? 'denied' : 'unavailable',
          message: error.code === 1
            ? '위치 권한이 거부됐어요. 브라우저 설정에서 허용하면 거리·가까운 순 정렬이 켜집니다.'
            : '위치를 가져오지 못했습니다. 실외에서 다시 시도해 주세요.',
        });
      },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 },
    );
    return true;
  }

  stop() {
    if (this.watchId != null && this.supported) navigator.geolocation.clearWatch(this.watchId);
    this.watchId = null;
  }
}
