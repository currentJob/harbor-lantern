/* Leaflet 지도 — 다크 타일 · 커스텀 핀 · 타일 실패 폴백 (설계서 §6.15 · NFR-015 · R4)
 *
 * 지도는 **없어도 되는 부품**이다. Leaflet 이 로드되지 않았거나 타일 서버가 죽어도
 * 목록·타임라인·경고·정산은 전부 그대로 동작해야 한다. 그래서 이 모듈의 모든 진입점은
 * "지도가 없다"를 정상 경로로 취급한다.
 *
 * 타일만이 유일하게 남은 런타임 외부 요청이다(CARTO dark_all — 원본과 같은 타일).
 * 나머지 자산은 전부 저장소 안에 있다(AC-042).
 */

import { escapeHtml } from './format.js';

const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const TILE_ATTRIBUTION = '© OpenStreetMap · © CARTO';
const TILE_ERROR_WINDOW_MS = 10000;
const TILE_ERROR_THRESHOLD = 5;

export class TripMap {
  constructor(containerId, { onMarkerClick, onTileTrouble } = {}) {
    this.containerId = containerId;
    this.onMarkerClick = onMarkerClick || (() => {});
    this.onTileTrouble = onTileTrouble || (() => {});
    this.map = null;
    this.markers = new Map();  // spot_id -> marker
    this.meMarker = null;
    this.tileErrors = [];
    this.available = false;
  }

  /** 지도를 세운다. 실패하면 false 를 돌려주고 앱은 계속 간다. */
  init() {
    const container = document.getElementById(this.containerId);
    if (!container) return false;
    if (typeof L === 'undefined') {
      // 벤더 파일이 빠졌거나 차단됐다. 지도 자리에 이유를 적고 넘어간다.
      this.onTileTrouble('지도 라이브러리를 불러오지 못했습니다 — 목록과 계산은 정상입니다.');
      container.classList.add('map-offline');
      return false;
    }
    try {
      this.map = L.map(this.containerId, { zoomControl: false, attributionControl: false })
        .setView([22.30, 114.10], 11);
      const tiles = L.tileLayer(TILE_URL, { subdomains: 'abcd', maxZoom: 19 });
      tiles.on('tileerror', () => this._noteTileError());
      tiles.addTo(this.map);
      L.control.attribution({ prefix: false }).addAttribution(TILE_ATTRIBUTION).addTo(this.map);
      this.available = true;
      setTimeout(() => this.invalidate(), 300);
      return true;
    } catch (error) {
      console.error('지도 초기화 실패', error);
      this.onTileTrouble('지도를 초기화하지 못했습니다 — 목록과 계산은 정상입니다.');
      container.classList.add('map-offline');
      this.map = null;
      this.available = false;
      return false;
    }
  }

  _noteTileError() {
    const now = Date.now();
    this.tileErrors = this.tileErrors.filter((t) => now - t < TILE_ERROR_WINDOW_MS);
    this.tileErrors.push(now);
    if (this.tileErrors.length >= TILE_ERROR_THRESHOLD) {
      const container = document.getElementById(this.containerId);
      if (container) container.classList.add('map-offline');
      this.onTileTrouble('지도 타일을 불러오지 못했습니다 — 목록과 계산은 정상입니다.');
      this.tileErrors = [];
    }
  }

  invalidate() {
    if (this.map) this.map.invalidateSize();
  }

  _pin(color, label) {
    return L.divIcon({
      className: '',
      iconSize: [22, 22],
      iconAnchor: [11, 22],
      popupAnchor: [0, -22],
      html: `<div class="pin" style="background:${color};color:${color}"><i>${label}</i></div>`,
    });
  }

  /** 서버 상태의 모든 스팟을 다시 찍는다. 일자 색이 곧 핀 색이다(원본과 동일). */
  render(days) {
    if (!this.map) return;
    for (const marker of this.markers.values()) marker.remove();
    this.markers.clear();
    for (const day of days || []) {
      day.spots.forEach((spot, index) => {
        const marker = L.marker([spot.lat, spot.lng], { icon: this._pin(day.color, index + 1) })
          .addTo(this.map)
          .bindPopup(
            `<b>${escapeHtml(spot.name)}</b><br>${escapeHtml(spot.time_label)} · `
            + `${escapeHtml(spot.name_original || day.title)}<br>🕐 ${escapeHtml(spot.hours_text || '정보 없음')}`,
          );
        marker.on('click', () => this.onMarkerClick(day.day_index, spot.id));
        this.markers.set(spot.id, marker);
      });
    }
  }

  focus(spot, zoom = 15) {
    if (!this.map || !spot) return;
    this.map.flyTo([spot.lat, spot.lng], zoom);
    const marker = this.markers.get(spot.id);
    if (marker) marker.openPopup();
  }

  focusDay(day) {
    if (!this.map || !day || !day.spots.length) return;
    this.map.flyTo([day.spots[0].lat, day.spots[0].lng], 13);
  }

  showMe(me) {
    if (!this.map || !me) return;
    if (!this.meMarker) {
      this.meMarker = L.marker([me.lat, me.lng], {
        icon: L.divIcon({ className: '', html: '<div class="me"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }),
      }).addTo(this.map);
      this.map.flyTo([me.lat, me.lng], 14);
    } else {
      this.meMarker.setLatLng([me.lat, me.lng]);
    }
  }

  flyToMe(me) {
    if (this.map && me) this.map.flyTo([me.lat, me.lng], 15);
  }
}
