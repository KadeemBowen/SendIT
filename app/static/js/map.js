/* global L */
export function makeMap(el, center) {
  const map = L.map(el, { zoomControl: true }).setView(center, 14);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(map);
  // The map container can change size as panels open/close.
  const resize = new ResizeObserver(() => map.invalidateSize());
  resize.observe(el);
  map.on('unload', () => resize.disconnect());
  return map;
}

export function pin(label, kind) {
  return L.divIcon({
    className: 'pin-icon',
    html: `<div class="pin pin-${kind}"><span>${label}</span></div>`,
    iconSize: [30, 30],
    iconAnchor: [15, 30],
    tooltipAnchor: [0, -28],
  });
}

export function riderIcon() {
  return L.divIcon({ className: 'pin-icon', html: '<div class="rider-dot"></div>', iconSize: [22, 22], iconAnchor: [11, 11] });
}

export function fit(map, points) {
  if (points.length === 1) map.setView(points[0], Math.max(map.getZoom(), 15));
  else if (points.length > 1) map.fitBounds(L.latLngBounds(points).pad(0.2), { maxZoom: 16 });
}

/** Draw an order (pickup, stops, drop-off, route, and optionally the rider) into a layer group. */
export function drawOrder(map, group, order, { fitView = true, showRider = true } = {}) {
  group.clearLayers();
  const points = [];
  const add = (lat, lng, label, kind, title) => {
    L.marker([lat, lng], { icon: pin(label, kind) }).bindTooltip(title).addTo(group);
    points.push([lat, lng]);
  };
  if (order.route && order.route.length > 1) {
    L.polyline(order.route, { color: '#0f766e', weight: 5, opacity: 0.75 }).addTo(group);
  }
  add(order.pickup_lat, order.pickup_lng, 'P', 'pickup', `Pickup: ${order.pickup_address}`);
  order.stops.forEach((s, i) => add(s.lat, s.lng, String(i + 1), 'stop', `Stop ${i + 1}: ${s.address}`));
  add(order.dropoff_lat, order.dropoff_lng, 'D', 'dropoff', `Drop-off: ${order.dropoff_address}`);
  if (showRider && order.rider_location) {
    const { lat, lng } = order.rider_location;
    L.marker([lat, lng], { icon: riderIcon(), zIndexOffset: 1000 }).bindTooltip('Rider').addTo(group);
    points.push([lat, lng]);
  }
  if (fitView) fit(map, points);
}
