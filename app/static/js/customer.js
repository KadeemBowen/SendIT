/* global L */
import { api } from './api.js';
import { drawOrder, fit, makeMap, pin } from './map.js';
import {
  ACTIVE_STATUSES, debounce, esc, fmtDate, fmtKm, fmtMin, money, paymentPill, statusPill, telLink, timeline, toast,
} from './ui.js';

const PRESET_TASKS = ['Collect a package', 'Buy items for me', 'Pay a bill', 'Drop off documents', 'Wait and bring back'];
const MAX_STOPS = 5;
const MAX_TASKS = 10;

export function renderCustomer(root, user, sock, cfg) {
  root.innerHTML = `
    <div class="layout">
      <div class="map-wrap">
        <div id="map" class="map"></div>
        <div id="map-hint" class="map-hint" hidden></div>
      </div>
      <section class="panel">
        <div id="active"></div>
        <button type="button" id="new-btn" class="btn btn-secondary btn-block" hidden>+ New delivery</button>
        <form id="book" class="card stack" autocomplete="off" novalidate>
          <div class="card-head">
            <h2>Book a delivery</h2>
            <button type="button" class="link-btn" id="close-form" hidden>Close</button>
          </div>
          <div class="places">
            ${placeField('pickup', 'Pickup', 'Search or tap the map')}
            <div id="stops"></div>
            ${placeField('dropoff', 'Drop-off', 'Search or tap the map')}
            <button type="button" class="link-btn" id="add-stop">+ Add a stop along the way</button>
          </div>

          <h3>What should the rider do?</h3>
          <div class="chips">${PRESET_TASKS.map(t => `<button type="button" class="chip" data-task="${esc(t)}">+ ${esc(t)}</button>`).join('')}</div>
          <ul id="tasks" class="task-list"></ul>
          <div class="row">
            <input id="task-input" placeholder="Or describe a task, e.g. Buy 2 bags of rice" maxlength="200">
            <button type="button" class="btn btn-secondary" id="task-add">Add</button>
          </div>

          <h3>Recipient <span class="muted small">(optional)</span></h3>
          <div class="row">
            <input id="rcp-name" placeholder="Name" maxlength="80">
            <input id="rcp-phone" placeholder="Phone" inputmode="tel" maxlength="30">
          </div>
          <textarea id="notes" rows="2" maxlength="500" placeholder="Notes for the rider (landmarks, gate code…)"></textarea>

          <h3>Payment</h3>
          <div class="choice">
            <label><input type="radio" name="pay" value="cash" checked><span><b>Cash</b><small>Pay the rider on delivery</small></span></label>
            <label><input type="radio" name="pay" value="mmg"><span><b>MMG</b><small>Mobile Money Guyana</small></span></label>
          </div>
          <input id="mmg-number" placeholder="MMG number that will pay" inputmode="tel" maxlength="30" value="${esc(user.phone)}" hidden>

          <div id="quote" class="quote" hidden></div>
          <button class="btn btn-primary btn-block" id="book-btn" disabled>Set pickup and drop-off</button>
        </form>
        <details class="card" id="history">
          <summary>Past deliveries</summary>
          <div id="history-list" class="history-list"></div>
        </details>
      </section>
    </div>`;

  const $ = sel => root.querySelector(sel);
  const form = $('#book');
  const activeEl = $('#active');
  const hint = $('#map-hint');
  const map = makeMap($('#map'), cfg.map_center);
  const draftLayer = L.layerGroup().addTo(map);
  const orderLayer = L.layerGroup().addTo(map);

  const s = {
    places: { pickup: null, dropoff: null },
    stops: [],          // each: place or null
    tasks: [],
    picking: null,      // which field the next map tap fills
    quote: null,
    quoteSeq: 0,
    active: [],
    history: [],
    trackedId: null,
    confirmCancel: null,
  };

  // ---------- places ----------

  function placeField(key, label, placeholder) {
    const kind = key.startsWith('stop') ? 'stop' : key;
    const tag = key === 'pickup' ? 'P' : key === 'dropoff' ? 'D' : String(Number(key.slice(5)) + 1);
    return `
      <div class="place" data-key="${key}">
        <span class="place-dot pin-${kind}">${tag}</span>
        <div class="place-body">
          <label for="in-${key}">${esc(label)}</label>
          <input id="in-${key}" class="place-input" placeholder="${esc(placeholder)}">
          <ul class="suggest" hidden></ul>
        </div>
        <div class="place-actions">
          ${key === 'pickup' ? '<button type="button" class="icon-btn" data-act="gps" title="Use my location" aria-label="Use my location">◎</button>' : ''}
          <button type="button" class="icon-btn" data-act="map" title="Pick on the map" aria-label="Pick on the map">⌖</button>
          ${kind === 'stop' ? '<button type="button" class="icon-btn" data-act="remove" title="Remove stop" aria-label="Remove stop">✕</button>' : ''}
        </div>
      </div>`;
  }

  const getPlace = key => (key.startsWith('stop-') ? s.stops[Number(key.slice(5))] : s.places[key]);

  function setPlace(key, place) {
    if (key.startsWith('stop-')) s.stops[Number(key.slice(5))] = place;
    else s.places[key] = place;
    const input = form.querySelector(`.place[data-key="${key}"] .place-input`);
    if (input) input.value = place ? place.address : '';
    drawDraft();
    requote();
  }

  async function placeAt(key, lat, lng) {
    setPlace(key, { lat, lng, address: `${lat.toFixed(5)}, ${lng.toFixed(5)}` });
    try {
      const { address } = await api(`/api/reverse?lat=${lat}&lng=${lng}`);
      const current = getPlace(key);
      if (address && current && current.lat === lat && current.lng === lng) setPlace(key, { lat, lng, address });
    } catch { /* keep coordinates as the label */ }
  }

  function renderStops() {
    $('#stops').innerHTML = s.stops.map((_, i) => placeField(`stop-${i}`, `Stop ${i + 1}`, 'Search or tap the map')).join('');
    s.stops.forEach((p, i) => { if (p) $(`#in-stop-${i}`).value = p.address; });
    $('#add-stop').hidden = s.stops.length >= MAX_STOPS;
  }

  function startPicking(key) {
    s.picking = key;
    const label = key === 'pickup' ? 'pickup' : key === 'dropoff' ? 'drop-off' : `stop ${Number(key.slice(5)) + 1}`;
    hint.textContent = `Tap the map to set the ${label}`;
    hint.hidden = false;
  }

  function stopPicking() {
    s.picking = null;
    hint.hidden = true;
  }

  map.on('click', e => {
    if (form.hidden) return;
    const key = s.picking || (!s.places.pickup ? 'pickup' : !s.places.dropoff ? 'dropoff' : null);
    if (!key) return;
    stopPicking();
    placeAt(key, e.latlng.lat, e.latlng.lng);
  });

  function drawDraft(refit = true) {
    draftLayer.clearLayers();
    const points = [];
    const add = (place, label, kind, key) => {
      if (!place) return;
      const marker = L.marker([place.lat, place.lng], { icon: pin(label, kind), draggable: true }).addTo(draftLayer);
      marker.on('dragend', () => {
        const ll = marker.getLatLng();
        placeAt(key, ll.lat, ll.lng);
      });
      points.push([place.lat, place.lng]);
    };
    if (s.quote && s.quote.route.length > 1) {
      L.polyline(s.quote.route, { color: '#0f766e', weight: 5, opacity: 0.6 }).addTo(draftLayer);
    }
    add(s.places.pickup, 'P', 'pickup', 'pickup');
    s.stops.forEach((p, i) => add(p, String(i + 1), 'stop', `stop-${i}`));
    add(s.places.dropoff, 'D', 'dropoff', 'dropoff');
    if (refit) fit(map, points);
  }

  // Address search suggestions
  const search = debounce(async input => {
    const list = input.parentElement.querySelector('.suggest');
    const q = input.value.trim();
    if (q.length < 3) { list.hidden = true; return; }
    try {
      const results = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
      if (input.value.trim() !== q) return;
      list.innerHTML = results.length
        ? results.map((r, i) => `<li><button type="button" data-i="${i}">${esc(r.address)}</button></li>`).join('')
        : '<li class="muted small">No matches - try tapping the map instead</li>';
      list._results = results;
      list.hidden = false;
    } catch (err) {
      list.innerHTML = `<li class="muted small">${esc(err.message)}</li>`;
      list.hidden = false;
    }
  }, 450);

  form.addEventListener('input', e => {
    if (e.target.classList.contains('place-input')) search(e.target);
  });

  form.addEventListener('focusout', e => {
    if (!e.target.classList.contains('place-input')) return;
    const list = e.target.parentElement.querySelector('.suggest');
    setTimeout(() => { list.hidden = true; }, 200);
  });

  form.addEventListener('click', e => {
    const choice = e.target.closest('.suggest button');
    if (choice) {
      const list = choice.closest('.suggest');
      const key = choice.closest('.place').dataset.key;
      setPlace(key, list._results[Number(choice.dataset.i)]);
      list.hidden = true;
      return;
    }
    const action = e.target.closest('[data-act]');
    if (action) {
      const key = action.closest('.place').dataset.key;
      if (action.dataset.act === 'map') startPicking(key);
      if (action.dataset.act === 'gps') useMyLocation(key, action);
      if (action.dataset.act === 'remove') {
        s.stops.splice(Number(key.slice(5)), 1);
        stopPicking();
        renderStops();
        drawDraft();
        requote();
      }
      return;
    }
    const chip = e.target.closest('[data-task]');
    if (chip) addTask(chip.dataset.task);
    const remove = e.target.closest('[data-remove-task]');
    if (remove) {
      s.tasks.splice(Number(remove.dataset.removeTask), 1);
      renderTasks();
      requote();
    }
  });

  function useMyLocation(key, button) {
    if (!navigator.geolocation) return toast('Location is not available on this device', 'error');
    button.disabled = true;
    navigator.geolocation.getCurrentPosition(
      pos => { button.disabled = false; placeAt(key, pos.coords.latitude, pos.coords.longitude); },
      err => {
        button.disabled = false;
        toast(window.isSecureContext ? `Couldn't get your location: ${err.message}` : 'Location needs HTTPS - tap the map instead', 'error');
      },
      { enableHighAccuracy: true, timeout: 15000 },
    );
  }

  $('#add-stop').onclick = () => {
    if (s.stops.length >= MAX_STOPS) return;
    s.stops.push(null);
    renderStops();
    startPicking(`stop-${s.stops.length - 1}`);
  };

  // ---------- tasks ----------

  function addTask(text) {
    const t = text.trim();
    if (!t) return;
    if (s.tasks.length >= MAX_TASKS) return toast(`Up to ${MAX_TASKS} tasks per delivery`, 'error');
    s.tasks.push(t);
    renderTasks();
    requote();
  }

  function renderTasks() {
    $('#tasks').innerHTML = s.tasks.map((t, i) => `
      <li><span>${esc(t)}</span><button type="button" class="icon-btn" data-remove-task="${i}" aria-label="Remove task">✕</button></li>`).join('');
  }

  $('#task-add').onclick = () => { addTask($('#task-input').value); $('#task-input').value = ''; };
  $('#task-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); $('#task-add').click(); }
  });

  // ---------- quote & booking ----------

  function tripPayload() {
    if (!s.places.pickup || !s.places.dropoff) return null;
    return { pickup: s.places.pickup, dropoff: s.places.dropoff, stops: s.stops.filter(Boolean), tasks: s.tasks };
  }

  const requote = debounce(async () => {
    const trip = tripPayload();
    const seq = ++s.quoteSeq;
    if (!trip) { s.quote = null; renderQuote(); return; }
    renderQuote(true);
    try {
      const q = await api('/api/quote', { method: 'POST', body: trip });
      if (seq !== s.quoteSeq) return;
      s.quote = q;
    } catch (err) {
      if (seq !== s.quoteSeq) return;
      s.quote = null;
      toast(err.message, 'error');
    }
    renderQuote();
    drawDraft(false);
  }, 400);

  function renderQuote(loading = false) {
    const el = $('#quote');
    const btn = $('#book-btn');
    if (loading) {
      el.hidden = false;
      el.innerHTML = '<p class="muted">Calculating price…</p>';
      btn.disabled = true;
      return;
    }
    if (!s.quote) {
      el.hidden = true;
      btn.disabled = true;
      btn.textContent = 'Set pickup and drop-off';
      return;
    }
    const q = s.quote;
    el.hidden = false;
    el.innerHTML = `
      <div class="quote-total"><span>Price</span><strong>${money(q.price, q.currency)}</strong></div>
      <p class="muted small">${fmtKm(q.distance_km)} · about ${fmtMin(q.duration_min)} riding${q.route_source === 'estimate' ? ' (estimated)' : ''}</p>
      <ul class="breakdown">${q.breakdown.map(b => `<li><span>${esc(b.label)}</span><span>${Math.round(b.amount).toLocaleString()}</span></li>`).join('')}</ul>`;
    btn.disabled = false;
    btn.textContent = `Book delivery · ${money(q.price, q.currency)}`;
  }

  const payMethod = () => form.querySelector('input[name=pay]:checked').value;
  form.querySelectorAll('input[name=pay]').forEach(r => {
    r.onchange = () => { $('#mmg-number').hidden = payMethod() !== 'mmg'; };
  });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const trip = tripPayload();
    if (!trip || !s.quote) return;
    const btn = $('#book-btn');
    btn.disabled = true;
    btn.textContent = 'Booking…';
    try {
      const order = await api('/api/orders', {
        method: 'POST',
        body: {
          ...trip,
          recipient_name: $('#rcp-name').value,
          recipient_phone: $('#rcp-phone').value,
          notes: $('#notes').value,
          payment_method: payMethod(),
          mmg_number: $('#mmg-number').value,
        },
      });
      resetForm();
      upsertOrder(order);
      s.trackedId = order.id;
      showForm(false);
      renderActive();
      drawTracked(true);
      toast(order.payment_status === 'pending'
        ? 'Delivery requested - approve the MMG payment on your phone'
        : 'Delivery requested - finding you a rider', 'success');
    } catch (err) {
      toast(err.message, 'error');
      renderQuote();
    }
  });

  function resetForm() {
    s.places = { pickup: null, dropoff: null };
    s.stops = [];
    s.tasks = [];
    s.quote = null;
    form.querySelectorAll('input:not([type=radio]):not(#mmg-number), textarea').forEach(i => { i.value = ''; });
    renderStops();
    renderTasks();
    renderQuote();
    draftLayer.clearLayers();
  }

  function showForm(show) {
    form.hidden = !show;
    $('#new-btn').hidden = show || !s.active.length;
    $('#close-form').hidden = !s.active.length;
    stopPicking();
    if (show) {
      map.removeLayer(orderLayer);
      drawDraft();
    } else {
      draftLayer.clearLayers();
      if (!map.hasLayer(orderLayer)) map.addLayer(orderLayer);
      drawTracked(true);
    }
  }

  $('#new-btn').onclick = () => showForm(true);
  $('#close-form').onclick = () => showForm(false);

  // ---------- active orders ----------

  function etaText(o) {
    if (o.status === 'requested') return 'Waiting for a rider to accept…';
    if (!o.eta) return o.rider_location ? '' : 'Waiting for the rider\'s location…';
    if (o.eta.next === 'pickup') {
      return `Rider reaches pickup in <b>~${fmtMin(o.eta.next_min)}</b> · delivery in ~${fmtMin(o.eta.delivery_min)}`;
    }
    return `Arriving at drop-off in <b>~${fmtMin(o.eta.next_min)}</b>`;
  }

  function orderCard(o) {
    const tracked = o.id === s.trackedId;
    const cancellable = o.status === 'requested' || o.status === 'accepted';
    return `
      <article class="card order ${tracked ? 'tracked' : ''}" data-id="${o.id}">
        <div class="card-head">
          ${statusPill(o.status)}
          <span class="muted small">#${o.id} · ${money(o.price, o.currency)}</span>
        </div>
        ${timeline(o.status)}
        <p class="eta">${etaText(o)}</p>
        ${o.rider ? `
          <div class="rider-info">
            <div class="avatar">${esc(o.rider.name.charAt(0))}</div>
            <div>
              <strong>${esc(o.rider.name)}</strong>
              <div class="muted small">${esc(o.rider.vehicle || '')}${o.rider.plate ? ` · ${esc(o.rider.plate)}` : ''}</div>
              <div class="small">${telLink(o.rider.phone)}</div>
            </div>
          </div>` : ''}
        <ul class="route-summary">
          <li><span class="place-dot pin-pickup">P</span>${esc(o.pickup_address)}</li>
          ${o.stops.map((st, i) => `<li><span class="place-dot pin-stop">${i + 1}</span>${esc(st.address)}</li>`).join('')}
          <li><span class="place-dot pin-dropoff">D</span>${esc(o.dropoff_address)}</li>
        </ul>
        ${o.tasks.length ? `<ul class="task-summary">${o.tasks.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
        ${paymentBlock(o)}
        ${cancellable ? (s.confirmCancel === o.id ? `
          <div class="confirm">
            <span>Cancel this delivery?</span>
            <button type="button" class="btn btn-danger" data-act="cancel-yes">Yes, cancel</button>
            <button type="button" class="btn btn-secondary" data-act="cancel-no">Keep it</button>
          </div>` : '<button type="button" class="link-btn danger" data-act="cancel">Cancel delivery</button>') : ''}
      </article>`;
  }

  function paymentBlock(o) {
    const mmg = o.payment_method === 'mmg';
    const text = {
      unpaid: mmg ? 'Not paid yet' : 'Pay the rider in cash on delivery',
      pending: o.payment?.message || 'Approve the payment request in MMG',
      paid: mmg ? 'Paid with MMG' : 'Paid',
      failed: o.payment?.message || 'The MMG payment didn\'t go through',
      refund_due: 'Refund on the way',
      refunded: 'Refunded',
      void: 'No charge',
    }[o.payment_status];
    let actions = '';
    if (o.payment_status === 'pending' && cfg.mmg_mode === 'mock' && o.payment) {
      actions = `
        <div class="test-mode small">
          <span>Test mode: pretend you</span>
          <button type="button" class="btn btn-secondary btn-sm" data-act="sim-paid" data-payment="${o.payment.id}">approved</button>
          <button type="button" class="btn btn-secondary btn-sm" data-act="sim-failed" data-payment="${o.payment.id}">declined</button>
        </div>`;
    } else if (o.payment_status === 'failed') {
      actions = `
        <div class="row">
          <input class="mmg-retry" data-id="${o.id}" value="${esc(o.mmg_number || user.phone)}" inputmode="tel" aria-label="MMG number">
          <button type="button" class="btn btn-primary btn-sm" data-act="pay-retry">Try again</button>
        </div>
        <button type="button" class="link-btn" data-act="pay-cash">Pay cash on delivery instead</button>`;
    }
    return `
      <div class="pay-box">
        <div class="card-head"><span class="small"><b>Payment</b></span>${paymentPill(o)}</div>
        <div class="small muted">${esc(text)}</div>
        ${actions}
      </div>`;
  }

  function renderActive() {
    // Live location updates re-render often; keep anything typed into retry fields.
    const typed = {};
    activeEl.querySelectorAll('.mmg-retry').forEach(i => { typed[i.dataset.id] = i.value; });
    const focusedId = document.activeElement?.classList.contains('mmg-retry') ? document.activeElement.dataset.id : null;
    activeEl.innerHTML = s.active.map(orderCard).join('');
    activeEl.querySelectorAll('.mmg-retry').forEach(i => {
      if (typed[i.dataset.id] != null) i.value = typed[i.dataset.id];
      if (i.dataset.id === focusedId) i.focus();
    });
    $('#new-btn').hidden = !form.hidden || !s.active.length;
  }

  activeEl.addEventListener('click', async e => {
    const card = e.target.closest('.order');
    if (!card) return;
    const id = Number(card.dataset.id);
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'cancel') { s.confirmCancel = id; renderActive(); return; }
    if (act === 'cancel-no') { s.confirmCancel = null; renderActive(); return; }
    if (act === 'cancel-yes') {
      s.confirmCancel = null;
      try { onOrder(await api(`/api/orders/${id}/cancel`, { method: 'POST' })); } catch (err) { toast(err.message, 'error'); renderActive(); }
      return;
    }
    if (act === 'sim-paid' || act === 'sim-failed') {
      const paymentId = e.target.closest('[data-payment]').dataset.payment;
      try {
        onOrder(await api(`/api/payments/${paymentId}/simulate`, { method: 'POST', body: { outcome: act === 'sim-paid' ? 'paid' : 'failed' } }));
      } catch (err) { toast(err.message, 'error'); }
      return;
    }
    if (act === 'pay-retry' || act === 'pay-cash') {
      const body = act === 'pay-cash' ? { method: 'cash' } : { method: 'mmg', mmg_number: card.querySelector('.mmg-retry').value };
      try {
        onOrder(await api(`/api/orders/${id}/payment`, { method: 'POST', body }));
        toast(act === 'pay-cash' ? 'OK - pay the rider in cash' : 'Payment request sent - approve it in MMG', 'success');
      } catch (err) { toast(err.message, 'error'); }
      return;
    }
    if (e.target.closest('a, input, button')) return;
    if (s.trackedId !== id) {
      s.trackedId = id;
      if (!form.hidden) showForm(false);
      renderActive();
      drawTracked(true);
    }
  });

  function drawTracked(refit) {
    const o = s.active.find(a => a.id === s.trackedId);
    if (!o) { orderLayer.clearLayers(); return; }
    if (!form.hidden) return;
    drawOrder(map, orderLayer, o, { fitView: refit });
  }

  function upsertOrder(o) {
    const i = s.active.findIndex(a => a.id === o.id);
    if (i >= 0) s.active[i] = o;
    else s.active.unshift(o);
  }

  function onOrder(o) {
    const prev = s.active.find(a => a.id === o.id);
    if (ACTIVE_STATUSES.includes(o.status)) {
      upsertOrder(o);
    } else {
      s.active = s.active.filter(a => a.id !== o.id);
      s.history = [o, ...s.history.filter(h => h.id !== o.id)];
      renderHistory();
    }
    if (prev && prev.payment_status !== o.payment_status) {
      if (o.payment_status === 'paid' && o.payment_method === 'mmg') toast('MMG payment received - thank you', 'success');
      if (o.payment_status === 'failed') toast('MMG payment failed - try again or pay cash', 'error');
    }
    if (prev && prev.status !== o.status) {
      const messages = {
        accepted: `${o.rider?.name || 'A rider'} accepted your delivery`,
        picked_up: 'Your rider has picked up',
        delivered: 'Delivered! Thanks for using us',
        cancelled: 'Delivery cancelled',
      };
      if (messages[o.status]) toast(messages[o.status], o.status === 'cancelled' ? 'info' : 'success');
    }
    if (!s.active.some(a => a.id === s.trackedId)) s.trackedId = s.active[0]?.id ?? null;
    renderActive();
    if (!s.active.length && form.hidden) showForm(true);
    else if (o.id === s.trackedId) drawTracked(!prev || prev.status !== o.status);
  }

  // ---------- history ----------

  function renderHistory() {
    $('#history-list').innerHTML = s.history.length
      ? s.history.map(o => `
        <div class="history-item">
          <div class="card-head">${statusPill(o.status)}<span class="small">${money(o.price, o.currency)}</span></div>
          <div class="small">${esc(o.pickup_address)} → ${esc(o.dropoff_address)}</div>
          <div class="muted small">${fmtDate(o.created_at)}${o.rider ? ` · ${esc(o.rider.name)}` : ''}</div>
          <div>${paymentPill(o)}</div>
        </div>`).join('')
      : '<p class="muted small">No past deliveries yet.</p>';
  }

  // ---------- load & live updates ----------

  async function refresh() {
    try {
      const orders = await api('/api/orders');
      s.active = orders.filter(o => ACTIVE_STATUSES.includes(o.status));
      s.history = orders.filter(o => !ACTIVE_STATUSES.includes(o.status));
      if (!s.active.some(a => a.id === s.trackedId)) s.trackedId = s.active[0]?.id ?? null;
      renderActive();
      renderHistory();
      if (!s.active.length) showForm(true);
      else if (form.hidden) drawTracked(true);
      else if (!s.places.pickup && !s.places.dropoff) showForm(false);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  sock.on(msg => {
    if (msg.type === 'open') refresh();
    if (msg.type === 'order') onOrder(msg.order);
  });

  renderStops();
  renderTasks();
}
