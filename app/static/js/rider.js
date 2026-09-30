/* global L */
import { api } from './api.js';
import { drawOrder, makeMap, riderIcon } from './map.js';
import {
  esc, fmtDate, fmtKm, fmtMin, haversineKm, money, navUrl, paymentPill, statusPill, telLink, timeline, toast,
} from './ui.js';

const SEND_EVERY_MS = 3000;
const SIM_TICK_MS = 2000;
const SIM_STEP_KM = 0.08;   // ~144 km/h: sped up so a demo trip takes a minute or two
const ARRIVED_KM = 0.03;

export function renderRider(root, user, sock, cfg) {
  root.innerHTML = `
    <div class="layout">
      <div class="map-wrap"><div id="map" class="map"></div></div>
      <section class="panel">
        <div class="card rider-bar">
          <label class="switch" title="Go online / offline">
            <input type="checkbox" id="online" ${user.is_online ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
          <div>
            <strong id="online-label"></strong>
            <div class="muted small" id="gps-status"></div>
          </div>
        </div>
        <div id="approval" class="card notice" hidden>
          <strong>Your account is waiting for approval</strong>
          <p class="small">We'll review your details and switch you on. You can go online as soon as you're approved.</p>
        </div>
        <label class="sim-toggle small"><input type="checkbox" id="sim"> Simulate GPS movement (for testing on a computer)</label>
        <div id="job"></div>
        <div id="requests"></div>
        <div class="card stats" id="stats"></div>
        <details class="card">
          <summary>Job history</summary>
          <div id="history-list" class="history-list"></div>
        </details>
      </section>
    </div>`;

  const $ = sel => root.querySelector(sel);
  const map = makeMap($('#map'), cfg.map_center);
  const jobLayer = L.layerGroup().addTo(map);
  let meMarker = null;

  const s = {
    online: user.is_online,
    me: null,
    job: null,
    requests: [],
    history: [],
    watchId: null,
    simTimer: null,
    simTargetIdx: 0,
    lastSentAt: 0,
    lastGpsError: '',
  };

  // ---------- location ----------

  function setMe(lat, lng) {
    const first = !s.me;
    s.me = { lat, lng };
    if (!meMarker) meMarker = L.marker([lat, lng], { icon: riderIcon(), zIndexOffset: 1000 }).bindTooltip('You').addTo(map);
    else meMarker.setLatLng([lat, lng]);
    if (first && !s.job) map.setView([lat, lng], 15);
    sendLocation();
    renderStatus();
    if (!s.job) renderRequests();
  }

  function sendLocation(force = false) {
    if (!s.me || (!s.online && !s.job)) return;
    const now = Date.now();
    if (!force && now - s.lastSentAt < SEND_EVERY_MS) return;
    if (sock.send({ type: 'location', ...s.me })) s.lastSentAt = now;
  }

  function sharing() {
    return s.online || !!s.job;
  }

  function startGps() {
    if (s.watchId !== null || !navigator.geolocation) return;
    if (!window.isSecureContext) {
      s.lastGpsError = 'GPS needs HTTPS. Use simulate mode, or open the app over HTTPS.';
      renderStatus();
      return;
    }
    s.watchId = navigator.geolocation.watchPosition(
      pos => { s.lastGpsError = ''; setMe(pos.coords.latitude, pos.coords.longitude); },
      err => { s.lastGpsError = `GPS: ${err.message}`; renderStatus(); },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 },
    );
  }

  function stopGps() {
    if (s.watchId !== null) navigator.geolocation.clearWatch(s.watchId);
    s.watchId = null;
  }

  function simWaypoints() {
    if (!s.job) return [];
    if (s.job.status === 'accepted') return [{ lat: s.job.pickup_lat, lng: s.job.pickup_lng }];
    if (s.job.status === 'picked_up') return [...s.job.stops, { lat: s.job.dropoff_lat, lng: s.job.dropoff_lng }];
    return [];
  }

  function simTick() {
    const target = simWaypoints()[s.simTargetIdx];
    if (!target || !s.me) { sendLocation(); return; }
    const remaining = haversineKm(s.me, target);
    if (remaining <= ARRIVED_KM) {
      s.simTargetIdx += 1;
      setMe(target.lat, target.lng);
      return;
    }
    const f = Math.min(1, SIM_STEP_KM / remaining);
    setMe(s.me.lat + (target.lat - s.me.lat) * f, s.me.lng + (target.lng - s.me.lng) * f);
  }

  function startSim() {
    if (s.simTimer) return;
    if (!s.me) {
      // Start a couple of km from the map centre so there is something to drive.
      const [lat, lng] = cfg.map_center;
      setMe(lat + (Math.random() - 0.5) * 0.03, lng + (Math.random() - 0.5) * 0.03);
    }
    s.simTimer = setInterval(simTick, SIM_TICK_MS);
  }

  function stopSim() {
    clearInterval(s.simTimer);
    s.simTimer = null;
  }

  function updateTracking() {
    const sim = $('#sim').checked;
    if (sharing() && sim) { stopGps(); startSim(); }
    else if (sharing()) { stopSim(); startGps(); }
    else { stopSim(); stopGps(); }
    renderStatus();
  }

  $('#sim').onchange = updateTracking;

  // ---------- online toggle ----------

  $('#online').onchange = async e => {
    const online = e.target.checked;
    try {
      await api('/api/rider/online', { method: 'POST', body: { online } });
      s.online = online;
      if (online) await loadRequests();
      else s.requests = [];
      renderRequests();
      updateTracking();
      sendLocation(true);
    } catch (err) {
      e.target.checked = !online;
      toast(err.message, 'error');
    }
  };

  function renderStatus() {
    $('#online-label').textContent = s.online ? 'Online' : 'Offline';
    $('#online').disabled = !user.approved;
    $('#approval').hidden = user.approved;
    let status;
    if (!user.approved) status = 'Pending approval';
    else if (!sharing()) status = 'Go online to receive delivery requests';
    else if (s.lastGpsError && !s.simTimer) status = s.lastGpsError;
    else if (!s.me) status = 'Waiting for your location…';
    else status = s.simTimer ? 'Sharing simulated location' : 'Sharing your live location';
    $('#gps-status').textContent = status;
  }

  // ---------- requests ----------

  async function loadRequests() {
    s.requests = await api('/api/orders/open');
  }

  function renderRequests() {
    const el = $('#requests');
    if (!s.online || s.job) { el.innerHTML = ''; return; }
    if (!s.requests.length) {
      el.innerHTML = '<div class="card empty"><p class="muted">No requests right now. New ones will appear here automatically.</p></div>';
      return;
    }
    el.innerHTML = `<h2 class="section-title">Open requests (${s.requests.length})</h2>` + s.requests.map(o => {
      const away = s.me ? haversineKm(s.me, { lat: o.pickup_lat, lng: o.pickup_lng }) : null;
      return `
        <article class="card request" data-id="${o.id}">
          <div class="card-head">
            <strong class="price">${money(o.price, o.currency)}</strong>
            <span class="muted small">${fmtKm(o.distance_km)} trip${away != null ? ` · pickup ${fmtKm(away)} away` : ''}</span>
          </div>
          <div>${paymentPill(o)}</div>
          <ul class="route-summary">
            <li><span class="place-dot pin-pickup">P</span>${esc(o.pickup_address)}</li>
            ${o.stops.length ? `<li class="muted small">+ ${o.stops.length} stop${o.stops.length > 1 ? 's' : ''}</li>` : ''}
            <li><span class="place-dot pin-dropoff">D</span>${esc(o.dropoff_address)}</li>
          </ul>
          ${o.tasks.length ? `<ul class="task-summary">${o.tasks.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
          <div class="row">
            <button type="button" class="btn btn-secondary" data-act="preview">Show on map</button>
            <button type="button" class="btn btn-primary" data-act="accept">Accept</button>
          </div>
        </article>`;
    }).join('');
  }

  $('#requests').addEventListener('click', async e => {
    const act = e.target.closest('[data-act]');
    if (!act) return;
    const id = Number(act.closest('.request').dataset.id);
    const order = s.requests.find(r => r.id === id);
    if (act.dataset.act === 'preview' && order) {
      drawOrder(map, jobLayer, order, { showRider: false });
      return;
    }
    if (act.dataset.act === 'accept') {
      act.disabled = true;
      try {
        setJob(await api(`/api/orders/${id}/accept`, { method: 'POST' }));
        s.requests = [];
        toast('Job accepted - head to the pickup', 'success');
      } catch (err) {
        toast(err.message, 'error');
        if (err.status === 409) s.requests = s.requests.filter(r => r.id !== id);
        renderRequests();
      }
    }
  });

  // ---------- current job ----------

  function setJob(order) {
    const statusChanged = !s.job || s.job.id !== order?.id || s.job.status !== order?.status;
    s.job = order && ['accepted', 'picked_up'].includes(order.status) ? order : null;
    if (statusChanged) s.simTargetIdx = 0;
    renderJob();
    renderRequests();
    if (s.job) drawOrder(map, jobLayer, s.job, { showRider: false, fitView: statusChanged });
    else jobLayer.clearLayers();
    updateTracking();
    sendLocation(true);
  }

  function taskKey(id) { return `job-${id}-tasks`; }

  function loadChecked(id) {
    try { return JSON.parse(localStorage.getItem(taskKey(id)) || '[]'); } catch { return []; }
  }

  function renderJob() {
    const el = $('#job');
    const o = s.job;
    if (!o) { el.innerHTML = ''; return; }
    const checked = loadChecked(o.id);
    const next = o.status === 'accepted'
      ? { label: 'Go to pickup', lat: o.pickup_lat, lng: o.pickup_lng }
      : { label: 'Go to drop-off', lat: o.dropoff_lat, lng: o.dropoff_lng };
    el.innerHTML = `
      <article class="card job">
        <div class="card-head">${statusPill(o.status)}<span class="muted small">#${o.id}</span></div>
        ${timeline(o.status)}
        <p class="eta">${esc(next.label)}${o.eta ? ` · <b>~${fmtMin(o.eta.next_min)}</b>` : ''}</p>

        <div class="job-section">
          <div class="muted small">Customer</div>
          <div>${esc(o.customer.name)} · ${telLink(o.customer.phone)}</div>
        </div>

        <ul class="route-summary nav-list">
          <li><span class="place-dot pin-pickup">P</span><span class="grow">${esc(o.pickup_address)}</span>
            <a class="btn btn-secondary btn-sm" href="${navUrl(o.pickup_lat, o.pickup_lng)}" target="_blank" rel="noopener">Navigate</a></li>
          ${o.stops.map((st, i) => `
            <li><span class="place-dot pin-stop">${i + 1}</span><span class="grow">${esc(st.address)}</span>
              <a class="btn btn-secondary btn-sm" href="${navUrl(st.lat, st.lng)}" target="_blank" rel="noopener">Navigate</a></li>`).join('')}
          <li><span class="place-dot pin-dropoff">D</span><span class="grow">${esc(o.dropoff_address)}
              ${o.recipient_name || o.recipient_phone ? `<br><span class="muted small">For ${esc(o.recipient_name || 'recipient')} ${telLink(o.recipient_phone)}</span>` : ''}</span>
            <a class="btn btn-secondary btn-sm" href="${navUrl(o.dropoff_lat, o.dropoff_lng)}" target="_blank" rel="noopener">Navigate</a></li>
        </ul>

        ${o.tasks.length ? `
          <div class="job-section">
            <div class="muted small">Tasks</div>
            <ul class="checklist">${o.tasks.map((t, i) => `
              <li><label><input type="checkbox" data-task="${i}" ${checked.includes(i) ? 'checked' : ''}> ${esc(t)}</label></li>`).join('')}
            </ul>
          </div>` : ''}
        ${o.notes ? `<div class="job-section"><div class="muted small">Notes</div><div>${esc(o.notes)}</div></div>` : ''}

        ${collectBlock(o)}
        <button type="button" class="btn btn-primary btn-block" data-act="advance">
          ${o.status === 'accepted' ? 'I\'ve picked up' : 'Mark as delivered'}
        </button>
      </article>`;
  }

  function collectBlock(o) {
    if (o.payment_status === 'paid') {
      return `<div class="quote-total"><span>Paid with ${o.payment_method === 'mmg' ? 'MMG' : 'cash'}</span><strong>Don't collect</strong></div>`;
    }
    if (o.payment_method === 'mmg') {
      return `
        <div class="quote-total"><span>MMG not paid yet</span><strong>${money(o.price, o.currency)}</strong></div>
        <p class="small muted">If it's still unpaid at drop-off, collect this amount in cash.</p>`;
    }
    return `<div class="quote-total"><span>Collect (cash)</span><strong>${money(o.price, o.currency)}</strong></div>`;
  }

  $('#job').addEventListener('change', e => {
    if (!e.target.matches('[data-task]') || !s.job) return;
    const done = [...$('#job').querySelectorAll('[data-task]:checked')].map(c => Number(c.dataset.task));
    try { localStorage.setItem(taskKey(s.job.id), JSON.stringify(done)); } catch { /* not persisted */ }
  });

  $('#job').addEventListener('click', async e => {
    const btn = e.target.closest('[data-act="advance"]');
    if (!btn || !s.job) return;
    const status = s.job.status === 'accepted' ? 'picked_up' : 'delivered';
    btn.disabled = true;
    try {
      const order = await api(`/api/orders/${s.job.id}/status`, { method: 'POST', body: { status } });
      if (status === 'delivered') {
        s.history = [order, ...s.history.filter(h => h.id !== order.id)];
        renderHistory();
        toast(`Delivered - ${money(order.price, order.currency)} earned`, 'success');
        if (s.online) await loadRequests();
      }
      setJob(order);
    } catch (err) {
      btn.disabled = false;
      toast(err.message, 'error');
    }
  });

  // ---------- history & stats ----------

  function renderHistory() {
    const done = s.history.filter(o => o.status === 'delivered');
    const today = new Date().toDateString();
    const todays = done.filter(o => new Date(o.delivered_at).toDateString() === today);
    const currency = cfg.currency;
    $('#stats').innerHTML = `
      <div><span class="muted small">Today</span><strong>${todays.length} job${todays.length === 1 ? '' : 's'}</strong></div>
      <div><span class="muted small">Earned today</span><strong>${money(todays.reduce((t, o) => t + o.price, 0), currency)}</strong></div>
      <div><span class="muted small">All time</span><strong>${done.length}</strong></div>`;
    $('#history-list').innerHTML = s.history.length
      ? s.history.map(o => `
        <div class="history-item">
          <div class="card-head">${statusPill(o.status)}<span class="small">${money(o.price, o.currency)}</span></div>
          <div class="small">${esc(o.pickup_address)} → ${esc(o.dropoff_address)}</div>
          <div class="muted small">${fmtDate(o.delivered_at || o.accepted_at || o.created_at)}</div>
        </div>`).join('')
      : '<p class="muted small">No jobs yet.</p>';
  }

  // ---------- load & live updates ----------

  async function refresh() {
    try {
      const mine = await api('/api/orders');
      s.history = mine.filter(o => !['accepted', 'picked_up'].includes(o.status));
      renderHistory();
      if (s.online) await loadRequests();
      setJob(mine.find(o => ['accepted', 'picked_up'].includes(o.status)) || null);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  sock.on(msg => {
    if (msg.type === 'open') { refresh(); return; }
    if (msg.type === 'account') {
      const wasApproved = user.approved;
      Object.assign(user, msg.user);
      s.online = user.is_online;
      $('#online').checked = s.online;
      if (!user.approved && wasApproved) { s.requests = []; renderRequests(); }
      updateTracking();
    }
    if (msg.type === 'order_new' && s.online) {
      if (!s.requests.some(r => r.id === msg.order.id)) s.requests.unshift(msg.order);
      if (!s.job && user.notifications) {
        toast(`New request · ${money(msg.order.price, msg.order.currency)}`, 'success');
        if (navigator.vibrate) navigator.vibrate([80, 60, 80]);
      }
      renderRequests();
    }
    // A job an admin assigned to this rider.
    if (msg.type === 'order' && !s.job && msg.order.rider?.id === user.id && ['accepted', 'picked_up'].includes(msg.order.status)) {
      s.requests = [];
      setJob(msg.order);
      return;
    }
    if (msg.type === 'order_gone') {
      s.requests = s.requests.filter(r => r.id !== msg.id);
      renderRequests();
    }
    if (msg.type === 'order' && s.job && msg.order.id === s.job.id) {
      if (msg.order.status === 'cancelled') {
        s.history = [msg.order, ...s.history];
        renderHistory();
        setJob(null);
        if (s.online) loadRequests().then(renderRequests).catch(() => {});
      } else if (msg.order.status !== 'delivered') {
        const statusChanged = msg.order.status !== s.job.status;
        const paymentChanged = msg.order.payment_status !== s.job.payment_status;
        s.job = msg.order;
        if (statusChanged) setJob(msg.order);
        else if (paymentChanged) renderJob();
        else renderJobEta();
      }
    }
  });

  function renderJobEta() {
    const el = $('#job .eta');
    const o = s.job;
    if (!el || !o) return;
    const label = o.status === 'accepted' ? 'Go to pickup' : 'Go to drop-off';
    el.innerHTML = `${esc(label)}${o.eta ? ` · <b>~${fmtMin(o.eta.next_min)}</b>` : ''}`;
  }

  renderStatus();
  renderHistory();
  updateTracking();
}
