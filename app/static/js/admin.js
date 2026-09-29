/* global L */
import { api } from './api.js';
import { fit, makeMap, pin, riderIcon } from './map.js';
import {
  ACTIVE_STATUSES, debounce, esc, fmtDate, fmtKm, money, paymentPill, statusPill, telLink, timeAgo, toast,
} from './ui.js';

const TABS = [
  ['overview', 'Overview'],
  ['orders', 'Orders'],
  ['riders', 'Riders'],
  ['customers', 'Customers'],
  ['pricing', 'Pricing'],
  ['payments', 'Payments'],
];

const ORDER_FILTERS = [
  ['all', 'All'],
  ['active', 'Active'],
  ['requested', 'Waiting for rider'],
  ['delivered', 'Delivered'],
  ['cancelled', 'Cancelled'],
  ['payment_issues', 'Payment issues'],
];

const PAYMENT_ROW_LABELS = { pending: 'Pending', paid: 'Paid', failed: 'Failed', refunded: 'Refunded', void: 'Void' };

export function renderAdmin(root, user, sock, cfg) {
  const cur = cfg.currency;
  root.innerHTML = `
    <div class="admin">
      <nav class="admin-tabs" role="tablist">
        ${TABS.map(([key, label]) => `
          <button type="button" role="tab" data-tab="${key}">${label}<span class="tab-badge" data-badge="${key}" hidden></span></button>`).join('')}
      </nav>
      <section class="admin-view" id="admin-view"></section>
    </div>`;
  const viewEl = root.querySelector('#admin-view');
  let current = {};

  const VIEWS = { overview: overviewView, orders: ordersView, riders: el => peopleView(el, 'rider'), customers: el => peopleView(el, 'customer'), pricing: pricingView, payments: paymentsView };

  function go(tab, opts = {}) {
    if (!VIEWS[tab]) tab = 'overview';
    try { history.replaceState(null, '', `#${tab}`); } catch { /* sandboxed */ }
    root.querySelectorAll('[data-tab]').forEach(b => {
      b.classList.toggle('active', b.dataset.tab === tab);
      b.setAttribute('aria-selected', String(b.dataset.tab === tab));
    });
    if (current.cleanup) current.cleanup();
    viewEl.innerHTML = '';
    current = VIEWS[tab](viewEl, opts) || {};
  }

  root.querySelector('.admin-tabs').addEventListener('click', e => {
    const b = e.target.closest('[data-tab]');
    if (b) go(b.dataset.tab);
  });

  function setBadge(tab, n) {
    const el = root.querySelector(`[data-badge="${tab}"]`);
    el.textContent = n;
    el.hidden = !n;
  }

  const refreshBadges = debounce(async () => {
    try {
      const { stats } = await api('/api/admin/overview');
      setBadge('orders', stats.waiting);
      setBadge('riders', stats.riders_pending);
      setBadge('payments', stats.refunds_due);
    } catch { /* ignore */ }
  }, 800);

  sock.on(msg => {
    if (msg.type === 'open') { refreshBadges(); if (current.refresh) current.refresh(); }
    if (msg.type === 'order') { refreshBadges(); if (current.onOrder) current.onOrder(msg.order); }
  });

  // ================= Overview =================

  function overviewView(el) {
    el.innerHTML = `
      <div class="admin-head"><h2>Today</h2><span class="muted small" id="ov-updated"></span></div>
      <div class="kpis" id="kpis"></div>
      <div class="alerts" id="alerts"></div>
      <div class="overview-grid">
        <div class="card map-card">
          <div class="card-head">
            <h3>Live map</h3>
            <span class="muted small legend"><span class="legend-dot"></span>Rider <span class="place-dot pin-pickup">P</span>Pickup <span class="place-dot pin-dropoff">D</span>Drop-off</span>
          </div>
          <div id="ov-map" class="ov-map"></div>
        </div>
        <div class="card">
          <h3>Active orders</h3>
          <div id="ov-active" class="compact-list"></div>
        </div>
      </div>`;
    const map = makeMap(el.querySelector('#ov-map'), cfg.map_center);
    const layer = L.layerGroup().addTo(map);
    let fitted = false;
    let alive = true;

    function tile(label, value, sub = '') {
      return `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-sub">${sub}</div></div>`;
    }

    async function load() {
      let data;
      try { data = await api('/api/admin/overview'); } catch (err) { toast(err.message, 'error'); return; }
      if (!alive) return;
      const st = data.stats;
      el.querySelector('#kpis').innerHTML = [
        tile('Orders today', st.orders_today, `${st.delivered_today} delivered · ${st.cancelled_today} cancelled`),
        tile('Revenue today', money(st.revenue_today, cur), 'from delivered orders'),
        tile('Waiting for a rider', st.waiting, st.waiting ? 'needs a rider now' : 'all picked up'),
        tile('In progress', st.in_progress, 'riders on the way'),
        tile('Riders online', st.riders_online, `${data.riders.filter(r => !r.job_id).length} available`),
      ].join('');

      const alerts = [];
      if (st.riders_pending) alerts.push(`<button type="button" class="alert" data-go="riders"><b>${st.riders_pending}</b> rider${st.riders_pending > 1 ? 's' : ''} waiting for approval →</button>`);
      if (st.refunds_due) alerts.push(`<button type="button" class="alert" data-go="payments"><b>${st.refunds_due}</b> MMG refund${st.refunds_due > 1 ? 's' : ''} due →</button>`);
      if (st.mmg_pending) alerts.push(`<button type="button" class="alert" data-go="orders" data-filter="payment_issues"><b>${st.mmg_pending}</b> MMG payment${st.mmg_pending > 1 ? 's' : ''} not confirmed →</button>`);
      el.querySelector('#alerts').innerHTML = alerts.join('');

      el.querySelector('#ov-active').innerHTML = data.active_orders.length
        ? data.active_orders.map(o => `
          <button type="button" class="compact-row" data-open="${o.id}">
            <span class="card-head"><b>#${o.id}</b>${statusPill(o.status)}</span>
            <span class="small">${esc(o.customer.name)} → ${o.rider ? esc(o.rider.name) : '<span class="muted">no rider yet</span>'}</span>
            <span class="muted small">${esc(o.pickup_address)} → ${esc(o.dropoff_address)} · ${timeAgo(o.created_at)}</span>
          </button>`).join('')
        : '<p class="muted small">No active orders right now.</p>';

      layer.clearLayers();
      const points = [];
      data.active_orders.forEach(o => {
        L.polyline(o.route.length > 1 ? o.route : [[o.pickup_lat, o.pickup_lng], [o.dropoff_lat, o.dropoff_lng]],
          { color: '#0f766e', weight: 3, opacity: 0.6 }).addTo(layer);
        L.marker([o.pickup_lat, o.pickup_lng], { icon: pin('P', 'pickup') }).bindTooltip(`#${o.id} pickup: ${o.pickup_address}`).addTo(layer);
        L.marker([o.dropoff_lat, o.dropoff_lng], { icon: pin('D', 'dropoff') }).bindTooltip(`#${o.id} drop-off: ${o.dropoff_address}`).addTo(layer);
        points.push([o.pickup_lat, o.pickup_lng], [o.dropoff_lat, o.dropoff_lng]);
      });
      data.riders.filter(r => r.lat != null).forEach(r => {
        L.marker([r.lat, r.lng], { icon: riderIcon(), zIndexOffset: 1000 })
          .bindTooltip(`${r.name} · ${r.job_id ? `on #${r.job_id}` : 'available'} · seen ${timeAgo(r.updated_at)}`)
          .addTo(layer);
        points.push([r.lat, r.lng]);
      });
      if (!fitted && points.length) { fit(map, points); fitted = true; }
      el.querySelector('#ov-updated').textContent = `Updated ${new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}`;
    }

    el.addEventListener('click', e => {
      const alert = e.target.closest('[data-go]');
      if (alert) go(alert.dataset.go, { filter: alert.dataset.filter });
      const row = e.target.closest('[data-open]');
      if (row) go('orders', { openId: Number(row.dataset.open), filter: 'active' });
    });

    load();
    const timer = setInterval(load, 10000);
    return { cleanup: () => { alive = false; clearInterval(timer); map.remove(); }, onOrder: debounce(load, 600), refresh: load };
  }

  // ================= Orders =================

  function ordersView(el, opts) {
    const st = { filter: opts.filter || 'all', q: '', orders: [], total: 0, selected: null, riders: [], confirmCancel: false };
    el.innerHTML = `
      <div class="admin-head"><h2>Orders</h2><span class="muted small" id="o-count"></span></div>
      <div class="toolbar">
        <div class="chips">${ORDER_FILTERS.map(([k, l]) => `<button type="button" class="chip" data-filter="${k}">${l}</button>`).join('')}</div>
        <input id="o-search" type="search" placeholder="Search: #id, address, customer, rider">
      </div>
      <div id="o-detail"></div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr><th>#</th><th>Created</th><th>Customer</th><th>Rider</th><th>Status</th><th>Payment</th><th class="num">Price</th></tr></thead>
          <tbody id="o-rows"></tbody>
        </table>
      </div>
      <button type="button" class="btn btn-secondary" id="o-more" hidden>Load more</button>`;
    const rowsEl = el.querySelector('#o-rows');
    const detailEl = el.querySelector('#o-detail');

    async function load(append = false) {
      const offset = append ? st.orders.length : 0;
      try {
        const res = await api(`/api/admin/orders?status=${st.filter}&q=${encodeURIComponent(st.q)}&limit=50&offset=${offset}`);
        st.orders = append ? [...st.orders, ...res.orders] : res.orders;
        st.total = res.total;
      } catch (err) { toast(err.message, 'error'); }
      renderRows();
    }

    function renderRows() {
      el.querySelectorAll('[data-filter]').forEach(c => c.classList.toggle('active', c.dataset.filter === st.filter));
      el.querySelector('#o-count').textContent = `${st.total} order${st.total === 1 ? '' : 's'}`;
      rowsEl.innerHTML = st.orders.length
        ? st.orders.map(o => `
          <tr data-id="${o.id}" class="${st.selected?.id === o.id ? 'selected' : ''}" tabindex="0">
            <td><b>#${o.id}</b></td>
            <td class="nowrap">${fmtDate(o.created_at)}</td>
            <td>${esc(o.customer.name)}</td>
            <td>${o.rider ? esc(o.rider.name) : '<span class="muted">-</span>'}</td>
            <td>${statusPill(o.status)}</td>
            <td>${paymentPill(o)}</td>
            <td class="num">${money(o.price, o.currency)}</td>
          </tr>`).join('')
        : '<tr><td colspan="7" class="muted">No orders match.</td></tr>';
      el.querySelector('#o-more').hidden = st.orders.length >= st.total;
    }

    async function select(order) {
      st.selected = order;
      st.confirmCancel = false;
      st.riders = [];
      renderRows();
      renderDetail();
      detailEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      if (order.status === 'requested') {
        try { st.riders = await api('/api/admin/riders/available'); } catch { st.riders = []; }
        renderDetail();
      }
    }

    function renderDetail() {
      const o = st.selected;
      if (!o) { detailEl.innerHTML = ''; return; }
      const events = [
        ['Booked', o.created_at],
        ['Rider assigned', o.accepted_at],
        ['Picked up', o.picked_up_at],
        ['Delivered', o.delivered_at],
        [`Cancelled${o.cancelled_by ? ` by ${o.cancelled_by}` : ''}`, o.cancelled_at],
      ].filter(([, t]) => t);
      const active = ACTIVE_STATUSES.includes(o.status);
      const canMarkPaid = o.status !== 'cancelled' && ['unpaid', 'pending', 'failed'].includes(o.payment_status);
      detailEl.innerHTML = `
        <article class="card order-detail">
          <div class="card-head"><h3>Order #${o.id}</h3><button type="button" class="link-btn" data-act="close">Close</button></div>
          <div class="pill-row">${statusPill(o.status)}${paymentPill(o)}</div>
          <div class="detail-grid">
            <div><div class="label">Customer</div>${esc(o.customer.name)}<div class="small">${telLink(o.customer.phone)}</div></div>
            <div><div class="label">Rider</div>${o.rider
              ? `${esc(o.rider.name)}<div class="small">${telLink(o.rider.phone)} · ${esc(o.rider.vehicle || '')} ${esc(o.rider.plate || '')}</div>`
              : '<span class="muted">Not assigned</span>'}</div>
            <div><div class="label">Price</div>${money(o.price, o.currency)}<div class="small muted">${fmtKm(o.distance_km)} · ${o.breakdown.map(b => `${esc(b.label)} ${Math.round(b.amount).toLocaleString()}`).join(' + ')}</div></div>
            <div><div class="label">Payment</div>${o.payment_method === 'mmg' ? `MMG ${esc(o.mmg_number || '')}` : 'Cash'}
              ${o.payment ? `<div class="small muted">${esc(o.payment.message || '')}${o.payment.provider_ref ? ` · ref ${esc(o.payment.provider_ref)}` : ''}</div>` : ''}</div>
          </div>
          <ul class="route-summary">
            <li><span class="place-dot pin-pickup">P</span>${esc(o.pickup_address)}</li>
            ${o.stops.map((s, i) => `<li><span class="place-dot pin-stop">${i + 1}</span>${esc(s.address)}</li>`).join('')}
            <li><span class="place-dot pin-dropoff">D</span>${esc(o.dropoff_address)}${o.recipient_name || o.recipient_phone ? ` <span class="muted small">(for ${esc(o.recipient_name || '')} ${esc(o.recipient_phone || '')})</span>` : ''}</li>
          </ul>
          ${o.tasks.length ? `<div><div class="label">Tasks</div><ul class="task-summary">${o.tasks.map(t => `<li>${esc(t)}</li>`).join('')}</ul></div>` : ''}
          ${o.notes ? `<div><div class="label">Notes</div>${esc(o.notes)}</div>` : ''}
          <ol class="events">${events.map(([label, t]) => `<li><span>${esc(label)}</span><span class="muted">${fmtDate(t)}</span></li>`).join('')}</ol>
          <div class="detail-actions">
            ${o.status === 'requested' ? `
              <div class="row">
                <select id="assign-rider" aria-label="Rider to assign">
                  ${st.riders.length
                    ? st.riders.map(r => `<option value="${r.id}">${esc(r.name)}${r.is_online ? ' (online)' : ''}</option>`).join('')
                    : '<option value="">No free riders</option>'}
                </select>
                <button type="button" class="btn btn-primary" data-act="assign" ${st.riders.length ? '' : 'disabled'}>Assign rider</button>
              </div>` : ''}
            ${o.payment_status === 'pending' && cfg.mmg_mode === 'mock' && o.payment ? `
              <div class="test-mode small"><span>Test mode: simulate MMG</span>
                <button type="button" class="btn btn-secondary btn-sm" data-act="sim-paid">Paid</button>
                <button type="button" class="btn btn-secondary btn-sm" data-act="sim-failed">Declined</button>
              </div>` : ''}
            <div class="row wrap">
              ${canMarkPaid ? '<button type="button" class="btn btn-secondary" data-act="mark-paid">Mark as paid</button>' : ''}
              ${o.payment_status === 'refund_due' ? '<button type="button" class="btn btn-primary" data-act="mark-refunded">Mark refund as sent</button>' : ''}
              ${active && !st.confirmCancel ? '<button type="button" class="btn btn-secondary danger-text" data-act="cancel">Cancel order</button>' : ''}
            </div>
            ${active && st.confirmCancel ? `
              <div class="confirm">
                <span>Cancel order #${o.id}? The customer and rider are told straight away.</span>
                <input id="cancel-reason" placeholder="Reason (optional)" maxlength="200">
                <button type="button" class="btn btn-danger" data-act="cancel-yes">Yes, cancel</button>
                <button type="button" class="btn btn-secondary" data-act="cancel-no">Keep it</button>
              </div>` : ''}
          </div>
        </article>`;
    }

    function applyOrder(o) {
      const i = st.orders.findIndex(x => x.id === o.id);
      if (i >= 0) st.orders[i] = o;
      if (st.selected?.id === o.id) {
        const statusChanged = st.selected.status !== o.status;
        st.selected = o;
        if (statusChanged) st.confirmCancel = false;
        // Don't wipe a reason being typed just because the rider moved.
        if (!st.confirmCancel) renderDetail();
      }
      renderRows();
    }

    detailEl.addEventListener('click', async e => {
      const act = e.target.closest('[data-act]')?.dataset.act;
      const o = st.selected;
      if (!act || !o) return;
      if (act === 'close') { st.selected = null; renderDetail(); renderRows(); return; }
      if (act === 'cancel') { st.confirmCancel = true; renderDetail(); return; }
      if (act === 'cancel-no') { st.confirmCancel = false; renderDetail(); return; }
      const calls = {
        assign: () => api(`/api/admin/orders/${o.id}/assign`, { method: 'POST', body: { rider_id: Number(el.querySelector('#assign-rider').value) } }),
        'cancel-yes': () => api(`/api/admin/orders/${o.id}/cancel`, { method: 'POST', body: { reason: el.querySelector('#cancel-reason').value } }),
        'mark-paid': () => api(`/api/admin/orders/${o.id}/mark-paid`, { method: 'POST' }),
        'mark-refunded': () => api(`/api/admin/orders/${o.id}/mark-refunded`, { method: 'POST' }),
        'sim-paid': () => api(`/api/payments/${o.payment.id}/simulate`, { method: 'POST', body: { outcome: 'paid' } }),
        'sim-failed': () => api(`/api/payments/${o.payment.id}/simulate`, { method: 'POST', body: { outcome: 'failed' } }),
      };
      if (!calls[act]) return;
      e.target.closest('button').disabled = true;
      try {
        st.confirmCancel = false;
        applyOrder(await calls[act]());
        renderDetail();
        toast('Saved', 'success');
      } catch (err) {
        toast(err.message, 'error');
        renderDetail();
      }
    });

    rowsEl.addEventListener('click', e => {
      const row = e.target.closest('tr[data-id]');
      if (row) select(st.orders.find(o => o.id === Number(row.dataset.id)));
    });
    rowsEl.addEventListener('keydown', e => {
      if (e.key === 'Enter' && e.target.matches('tr[data-id]')) e.target.click();
    });
    el.querySelector('.chips').addEventListener('click', e => {
      const chip = e.target.closest('[data-filter]');
      if (chip) { st.filter = chip.dataset.filter; load(); }
    });
    el.querySelector('#o-search').addEventListener('input', debounce(e => { st.q = e.target.value; load(); }, 350));
    el.querySelector('#o-more').onclick = () => load(true);

    const reload = debounce(() => load(), 800);
    load().then(async () => {
      if (!opts.openId) return;
      const found = st.orders.find(o => o.id === opts.openId);
      try { select(found || await api(`/api/orders/${opts.openId}`)); } catch { /* gone */ }
    });

    return {
      onOrder(o) {
        if (st.orders.some(x => x.id === o.id) || st.selected?.id === o.id) applyOrder(o);
        else reload();
      },
      refresh: () => load(),
    };
  }

  // ================= Riders & customers =================

  function peopleView(el, role) {
    const isRider = role === 'rider';
    const st = { q: '', people: [], confirm: null };
    el.innerHTML = `
      <div class="admin-head"><h2>${isRider ? 'Riders' : 'Customers'}</h2><span class="muted small" id="p-count"></span></div>
      <div class="toolbar"><input id="p-search" type="search" placeholder="Search name, email, phone${isRider ? ', plate' : ''}"></div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr>
            <th>Name</th><th>Phone</th>${isRider ? '<th>Vehicle</th>' : ''}<th>Status</th>
            ${isRider ? '<th>Last seen</th><th class="num">Jobs</th><th class="num">Earned</th>' : '<th class="num">Orders</th><th class="num">Spent</th><th>Joined</th>'}
            <th></th>
          </tr></thead>
          <tbody id="p-rows"></tbody>
        </table>
      </div>`;
    const rowsEl = el.querySelector('#p-rows');

    function statusOf(p) {
      if (!p.active) return '<span class="status status-cancelled">Suspended</span>';
      if (isRider && !p.approved) return '<span class="status status-requested">Pending approval</span>';
      if (isRider && p.is_online) return '<span class="status status-delivered">Online</span>';
      return '<span class="status">Active</span>';
    }

    function actions(p) {
      if (st.confirm === p.id) {
        return `<span class="nowrap">Suspend? <button type="button" class="btn btn-danger btn-sm" data-act="suspend-yes">Yes</button>
                <button type="button" class="btn btn-secondary btn-sm" data-act="suspend-no">No</button></span>`;
      }
      if (!p.active) return '<button type="button" class="btn btn-secondary btn-sm" data-act="reactivate">Reactivate</button>';
      return `<span class="nowrap">
        ${isRider && !p.approved ? '<button type="button" class="btn btn-primary btn-sm" data-act="approve">Approve</button>' : ''}
        <button type="button" class="btn btn-secondary btn-sm" data-act="suspend">Suspend</button></span>`;
    }

    function render() {
      el.querySelector('#p-count').textContent = `${st.people.length} ${isRider ? 'rider' : 'customer'}${st.people.length === 1 ? '' : 's'}`;
      rowsEl.innerHTML = st.people.length
        ? st.people.map(p => `
          <tr data-id="${p.id}">
            <td><b>${esc(p.name)}</b><div class="muted small">${esc(p.email)}</div></td>
            <td class="nowrap">${telLink(p.phone)}</td>
            ${isRider ? `<td>${esc(p.vehicle || '')}<div class="muted small">${esc(p.plate || '')}</div></td>` : ''}
            <td>${statusOf(p)}</td>
            ${isRider
              ? `<td class="nowrap small">${p.last_seen ? timeAgo(p.last_seen) : '<span class="muted">never</span>'}</td>
                 <td class="num">${p.jobs}</td><td class="num">${money(p.total, cur)}</td>`
              : `<td class="num">${p.jobs}</td><td class="num">${money(p.total, cur)}</td><td class="nowrap small">${fmtDate(p.created_at)}</td>`}
            <td class="num">${actions(p)}</td>
          </tr>`).join('')
        : `<tr><td colspan="8" class="muted">No ${isRider ? 'riders' : 'customers'} found.</td></tr>`;
    }

    async function load() {
      try { st.people = await api(`/api/admin/users?role=${role}&q=${encodeURIComponent(st.q)}`); } catch (err) { toast(err.message, 'error'); }
      render();
    }

    rowsEl.addEventListener('click', async e => {
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (!act) return;
      const id = Number(e.target.closest('tr').dataset.id);
      if (act === 'suspend') { st.confirm = id; render(); return; }
      if (act === 'suspend-no') { st.confirm = null; render(); return; }
      const body = { approve: { approved: true }, 'suspend-yes': { active: false }, reactivate: { active: true } }[act];
      st.confirm = null;
      try {
        const updated = await api(`/api/admin/users/${id}`, { method: 'PATCH', body });
        const p = st.people.find(x => x.id === id);
        Object.assign(p, updated);
        toast(act === 'approve' ? `${p.name} approved` : act === 'reactivate' ? `${p.name} reactivated` : `${p.name} suspended`, 'success');
        refreshBadges();
      } catch (err) { toast(err.message, 'error'); }
      render();
    });
    el.querySelector('#p-search').addEventListener('input', debounce(e => { st.q = e.target.value; load(); }, 350));
    load();
    return { refresh: load };
  }

  // ================= Pricing =================

  function pricingView(el) {
    const FIELDS = [
      ['base_fee', 'Base fee', 'Charged on every delivery'],
      ['per_km', 'Per km', 'Road distance from pickup through stops to drop-off'],
      ['per_extra_stop', 'Per extra stop', 'Each stop between pickup and drop-off'],
      ['per_task', 'Per task', 'Each thing the rider is asked to do'],
      ['minimum', 'Minimum fare', 'No delivery costs less than this'],
      ['round_to', 'Round up to nearest', 'e.g. 100 makes every price end in 00'],
    ];
    let saved = null;
    let defaults = null;
    el.innerHTML = `
      <div class="admin-head"><h2>Pricing</h2><span class="muted small">Changes apply to new quotes straight away</span></div>
      <div class="pricing-grid">
        <form class="card stack" id="rates-form" novalidate>
          <h3>Rates (${esc(cur)})</h3>
          ${FIELDS.map(([k, label, help]) => `
            <label class="field">
              <span><b>${label}</b><small class="muted">${help}</small></span>
              <input type="number" name="${k}" min="${k === 'round_to' ? 1 : 0}" step="any" required>
            </label>`).join('')}
          <div class="row wrap">
            <button class="btn btn-primary" id="save-rates">Save rates</button>
            <button type="button" class="btn btn-secondary" id="reset-rates">Fill in defaults</button>
          </div>
        </form>
        <div class="card stack">
          <h3>Try a trip</h3>
          <div class="row">
            <label class="field-sm">Distance (km)<input type="number" id="pv-km" value="5" min="0" step="0.1"></label>
            <label class="field-sm">Extra stops<input type="number" id="pv-stops" value="0" min="0" max="20"></label>
            <label class="field-sm">Tasks<input type="number" id="pv-tasks" value="1" min="0" max="20"></label>
          </div>
          <div id="pv-out" class="quote"></div>
        </div>
      </div>`;
    const form = el.querySelector('#rates-form');
    const readRates = () => Object.fromEntries(FIELDS.map(([k]) => [k, Number(form.elements[k].value)]));
    const fill = rates => FIELDS.forEach(([k]) => { form.elements[k].value = rates[k]; });

    const preview = debounce(async () => {
      const trip = {
        distance_km: Number(el.querySelector('#pv-km').value) || 0,
        stops: Number(el.querySelector('#pv-stops').value) || 0,
        tasks: Number(el.querySelector('#pv-tasks').value) || 0,
      };
      const out = el.querySelector('#pv-out');
      try {
        const [now, next] = await Promise.all([
          api('/api/admin/pricing/preview', { method: 'POST', body: { ...trip, rates: saved } }),
          api('/api/admin/pricing/preview', { method: 'POST', body: { ...trip, rates: readRates() } }),
        ]);
        const changed = now.price !== next.price;
        out.innerHTML = `
          <div class="quote-total"><span>${changed ? 'New price' : 'Price'}</span><strong>${money(next.price, cur)}</strong></div>
          ${changed ? `<p class="small muted">Currently ${money(now.price, cur)} (${next.price > now.price ? '+' : ''}${money(next.price - now.price, cur)})</p>` : ''}
          <ul class="breakdown">${next.breakdown.map(b => `<li><span>${esc(b.label)}</span><span>${Math.round(b.amount).toLocaleString()}</span></li>`).join('')}</ul>`;
      } catch (err) {
        out.innerHTML = `<p class="error small">${esc(err.message)}</p>`;
      }
    }, 300);

    form.addEventListener('input', preview);
    el.querySelectorAll('#pv-km, #pv-stops, #pv-tasks').forEach(i => i.addEventListener('input', preview));
    el.querySelector('#reset-rates').onclick = () => { fill(defaults); preview(); };
    form.addEventListener('submit', async e => {
      e.preventDefault();
      try {
        const res = await api('/api/admin/pricing', { method: 'PUT', body: readRates() });
        saved = res.rates;
        fill(saved);
        preview();
        toast('Rates saved - new quotes use them now', 'success');
      } catch (err) { toast(err.message, 'error'); }
    });

    api('/api/admin/pricing').then(res => {
      saved = res.rates;
      defaults = res.defaults;
      fill(saved);
      preview();
    }).catch(err => toast(err.message, 'error'));
    return {};
  }

  // ================= Payments =================

  function paymentsView(el) {
    el.innerHTML = `
      <div class="admin-head"><h2>Payments</h2></div>
      <div id="mmg-status"></div>
      <div id="refunds"></div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr><th>#</th><th>Order</th><th>Customer</th><th>Method</th><th>Status</th><th>Reference / note</th><th>Updated</th><th class="num">Amount</th></tr></thead>
          <tbody id="pay-rows"></tbody>
        </table>
      </div>`;

    async function load() {
      let data;
      try { data = await api('/api/admin/payments'); } catch (err) { toast(err.message, 'error'); return; }
      const m = data.mmg;
      el.querySelector('#mmg-status').innerHTML = m.mode === 'mock'
        ? `<div class="card notice"><strong>MMG is in test mode</strong>
             <p class="small">MMG payments are simulated, and customers and admins get buttons to approve or decline them. To take real payments, add MMG's merchant API to <code>app/payments.py</code> (<code>LiveMMG</code>), set the MMG credentials, and start the server with <code>COURIER_MMG_MODE=live</code>.</p></div>`
        : m.configured
          ? '<div class="card notice ok"><strong>MMG live</strong><p class="small">Real MMG payments are on.</p></div>'
          : '<div class="card notice"><strong>MMG live mode is on but not configured</strong><p class="small">Set MMG_API_BASE, MMG_MERCHANT_ID and MMG_API_KEY. Until then, MMG requests fail and customers are asked to pay cash.</p></div>';

      const refundOrders = [...new Map(data.payments.filter(p => p.order_payment_status === 'refund_due').map(p => [p.order_id, p])).values()];
      el.querySelector('#refunds').innerHTML = refundOrders.length ? `
        <div class="card stack">
          <h3>Refunds due</h3>
          ${refundOrders.map(p => `
            <div class="refund-row">
              <span><b>Order #${p.order_id}</b> · ${esc(p.customer)} · MMG ${esc(p.mmg_number || '')} · ${money(p.amount, p.currency)}</span>
              <button type="button" class="btn btn-primary btn-sm" data-refund="${p.order_id}">Mark refund as sent</button>
            </div>`).join('')}
        </div>` : '';

      el.querySelector('#pay-rows').innerHTML = data.payments.length
        ? data.payments.map(p => `
          <tr>
            <td>${p.id}</td>
            <td><button type="button" class="link-btn" data-open="${p.order_id}">#${p.order_id}</button></td>
            <td>${esc(p.customer)}</td>
            <td>${{ mmg: 'MMG', cash: 'Cash', manual: 'Manual' }[p.provider] || esc(p.provider)}</td>
            <td><span class="status pay-${p.status}">${PAYMENT_ROW_LABELS[p.status] || esc(p.status)}</span></td>
            <td class="small">${esc(p.provider_ref || '')}${p.provider_ref && p.message ? '<br>' : ''}<span class="muted">${esc(p.message || '')}</span></td>
            <td class="nowrap small">${fmtDate(p.updated_at)}</td>
            <td class="num">${money(p.amount, p.currency)}</td>
          </tr>`).join('')
        : '<tr><td colspan="8" class="muted">No payments yet.</td></tr>';
    }

    el.addEventListener('click', async e => {
      const refund = e.target.closest('[data-refund]');
      if (refund) {
        refund.disabled = true;
        try {
          await api(`/api/admin/orders/${refund.dataset.refund}/mark-refunded`, { method: 'POST' });
          toast('Refund recorded', 'success');
          refreshBadges();
        } catch (err) { toast(err.message, 'error'); }
        load();
      }
      const open = e.target.closest('[data-open]');
      if (open) go('orders', { openId: Number(open.dataset.open) });
    });

    load();
    return { onOrder: debounce(load, 800), refresh: load };
  }

  go(location.hash.slice(1));
}
