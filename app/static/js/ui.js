export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function money(amount, currency) {
  return `${currency} ${Math.round(amount).toLocaleString()}`;
}

export function fmtMin(min) {
  if (min == null) return '';
  if (min < 1) return 'under a minute';
  if (min < 60) return `${Math.round(min)} min`;
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}

export function fmtKm(km) {
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}

export function fmtDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

let toastTimer;
export function toast(message, kind = 'info') {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.className = `toast toast-${kind}`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

export const STATUS_LABELS = {
  requested: 'Finding a rider',
  accepted: 'Rider on the way',
  picked_up: 'Picked up',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
};

export const ACTIVE_STATUSES = ['requested', 'accepted', 'picked_up'];

export const PAYMENT_LABELS = {
  unpaid: 'Unpaid',
  pending: 'Awaiting MMG',
  paid: 'Paid',
  failed: 'Failed',
  refund_due: 'Refund due',
  refunded: 'Refunded',
  void: 'No charge',
};

export function paymentPill(o) {
  const method = o.payment_method === 'mmg' ? 'MMG' : 'Cash';
  const label = o.payment_method === 'cash' && o.payment_status === 'unpaid' ? 'on delivery' : PAYMENT_LABELS[o.payment_status];
  return `<span class="status pay-${o.payment_status}">${method} · ${label}</span>`;
}

export function timeAgo(iso) {
  if (!iso) return '';
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  if (min < 1440) return `${Math.floor(min / 60)} h ago`;
  return fmtDate(iso);
}

const STEPS = [['requested', 'Requested'], ['accepted', 'Rider assigned'], ['picked_up', 'Picked up'], ['delivered', 'Delivered']];

export function timeline(status) {
  const idx = STEPS.findIndex(([key]) => key === status);
  return `<ol class="timeline">${STEPS.map(([, label], i) =>
    `<li class="${i < idx || status === 'delivered' ? 'done' : i === idx ? 'current' : ''}">${label}</li>`).join('')}</ol>`;
}

export function statusPill(status) {
  return `<span class="status status-${status}">${STATUS_LABELS[status] || status}</span>`;
}

export function haversineKm(a, b) {
  const rad = d => d * Math.PI / 180;
  const dLat = rad(b.lat - a.lat);
  const dLng = rad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

export function navUrl(lat, lng) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
}

export function telLink(phone) {
  return phone ? `<a href="tel:${esc(phone.replace(/[^\d+]/g, ''))}">${esc(phone)}</a>` : '';
}
