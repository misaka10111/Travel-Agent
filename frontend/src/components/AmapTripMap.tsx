import { useEffect, useRef } from 'react';
import { load } from '@amap/amap-jsapi-loader';
import type { Draft, Place } from '../api/planning';

type Coordinate = { longitude: number; latitude: number; crs: string };
type Props = {
  plan: Draft;
  dayIndex: number;
  places: Map<string, Place>;
  alternatives: Place[];
  focusPlace: string | null;
  onFocus: (placeId: string) => void;
  onUnavailable: () => void;
};

const position = (point: Coordinate): [number, number] => [point.longitude, point.latitude];
const isDomesticPoint = (point: Coordinate | null | undefined): point is Coordinate =>
  !!point && point.crs === 'GCJ02' && Number.isFinite(point.longitude) && Number.isFinite(point.latitude);

// The map is a view of the structured plan; route queries stay on the backend.
export function AmapTripMap({ plan, dayIndex, places, alternatives, focusPlace, onFocus, onUnavailable }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const markerPositions = useRef<Map<string, [number, number]>>(new Map());

  useEffect(() => {
    if (!focusPlace || !mapRef.current) return;
    const target = markerPositions.current.get(focusPlace);
    if (target) mapRef.current.setZoomAndCenter(14, target);
  }, [focusPlace, dayIndex, plan.version]);

  useEffect(() => {
    const day = plan.days.find(item => item.day_index === dayIndex);
    const stops = day?.stops.map(stop => ({ stop, place: places.get(stop.place_id) }))
      .filter((entry): entry is { stop: typeof day.stops[number]; place: Place } =>
        !!entry.place && isDomesticPoint(entry.place.coordinates)) ?? [];
    if (!container.current || !stops.length) return;
    let cancelled = false;
    let map: any = null;
    window._AMapSecurityConfig = { serviceHost: `${window.location.origin}/_AMapService` };
    load({ key: import.meta.env.VITE_AMAP_JS_KEY, version: '2.0', plugins: ['AMap.Scale'] })
      .then(AMap => {
        if (cancelled || !container.current) return;
        map = new AMap.Map(container.current, { viewMode: '2D', zoom: 12,
          center: position(stops[0].place.coordinates!), showOversea: false });
        mapRef.current = map;
        map.addControl(new AMap.Scale());
        const overlays: any[] = [];
        const positions = new Map<string, [number, number]>();
        stops.forEach(({ stop, place }, index) => {
          const at = position(place.coordinates!);
          positions.set(place.place_id, at);
          const pin = document.createElement('span');
          pin.className = `ta-amap-pin ${stop.category === 'hotel' ? 'hotel' : ''}`;
          pin.textContent = stop.category === 'hotel' ? 'H' : String(index + 1);
          pin.addEventListener('click', event => { event.stopPropagation(); onFocus(place.place_id); });
          const marker = new AMap.Marker({ position: at, title: place.name, anchor: 'bottom-center',
            content: pin });
          marker.on('click', () => onFocus(place.place_id));
          overlays.push(marker);
        });
        const occupied = new Set(day!.stops.flatMap(stop => [stop.place_id, ...stop.child_place_ids]));
        const nearby = alternatives.filter(place => (place.category === 'hotel' || place.category === 'attraction') &&
          !occupied.has(place.place_id) && isDomesticPoint(place.coordinates))
          .map(place => ({ place, distance: Math.min(...stops.map(({ place: anchor }) => {
            const a = anchor.coordinates!; const b = place.coordinates!;
            return Math.hypot((a.longitude - b.longitude) * Math.cos(a.latitude * Math.PI / 180) * 111320,
              (a.latitude - b.latitude) * 111320);
          })) }))
          .filter(item => item.distance <= 3000)
          .sort((a, b) => a.distance - b.distance || a.place.place_id.localeCompare(b.place.place_id))
          .slice(0, 10);
        nearby.forEach(({ place }) => {
          const at = position(place.coordinates!);
          positions.set(place.place_id, at);
          const marker = new AMap.CircleMarker({ center: at, radius: 6, strokeColor: '#586ad7',
            strokeWeight: 2, fillColor: '#fff', fillOpacity: 1, cursor: 'pointer', title: place.name });
          marker.on('click', () => onFocus(place.place_id));
          overlays.push(marker);
        });
        let routeIndex = 0;
        day!.stops.slice(1).forEach((stop, index) => {
          const before = day!.stops[index];
          if (before.place_id === stop.place_id) return;
          const route = day!.routes[routeIndex++];
          const from = places.get(before.place_id)?.coordinates;
          const to = places.get(stop.place_id)?.coordinates;
          if (!isDomesticPoint(from) || !isDomesticPoint(to)) return;
          const matches = route?.from_place_id === before.place_id && route.to_place_id === stop.place_id;
          const trace = matches && route.status === 'ok' && route.geometry?.crs === 'GCJ02' &&
            route.geometry.encoding === 'points' && route.geometry.points.length > 1 ? route.geometry.points : null;
          const path = trace ? trace.filter(isDomesticPoint).map(position) : [position(from), position(to)];
          if (path.length < 2) return;
          overlays.push(new AMap.Polyline({ path, strokeColor: trace ? '#4867d9' : '#b7bfcc',
            strokeWeight: trace ? 5 : 2, strokeStyle: trace ? 'solid' : 'dashed',
            showDir: !!trace, zIndex: trace ? 50 : 20 }));
        });
        markerPositions.current = positions;
        map.add(overlays);
        map.setFitView(overlays, false, [24, 24, 24, 24]);
        if (focusPlace && positions.has(focusPlace)) map.setZoomAndCenter(14, positions.get(focusPlace));
      })
      .catch(() => { if (!cancelled) onUnavailable(); });
    return () => { cancelled = true; mapRef.current = null; markerPositions.current.clear(); map?.destroy(); };
  }, [plan.plan_id, plan.version, dayIndex, places, alternatives]);

  return <div className="ta-amap-map" ref={container} role="img" aria-label={`第 ${dayIndex} 天可缩放的高德行程地图`} />;
}

declare global {
  interface Window { _AMapSecurityConfig?: { serviceHost: string } }
}
