import { api } from './api.js';
import { esc, timeAgo, toast } from './ui.js';

const BELL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>';

/** Bell button with unread count and a drop-down list. `user.notifications` turns it on or off. */
export function initNotifications(container, overlays, sock, user) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'top-btn';
  button.setAttribute('aria-label', 'Notifications');
  button.innerHTML = `${BELL}<span class="bell-badge" hidden></span>`;
  container.appendChild(button);
  const badge = button.querySelector('.bell-badge');

  const panel = document.createElement('div');
  panel.className = 'popover';
  panel.hidden = true;
  overlays.appendChild(panel);

  const s = { items: [], unread: 0 };

  function renderBadge() {
    const show = user.notifications && s.unread > 0;
    badge.hidden = !show;
    badge.textContent = s.unread > 9 ? '9+' : s.unread;
    button.setAttribute('aria-label', show ? `Notifications, ${s.unread} unread` : 'Notifications');
  }

  function renderPanel() {
    let body;
    if (!user.notifications) {
      body = '<p class="notif-empty">Notifications are turned off.<br>Turn them on from your profile.</p>';
    } else if (!s.items.length) {
      body = '<p class="notif-empty">Nothing yet. Updates about your deliveries will show up here.</p>';
    } else {
      body = s.items.map(n => `
        <div class="notif ${n.read_at ? '' : 'unread'}">
          <span class="notif-dot"></span>
          <div class="notif-body">
            <strong>${esc(n.title)}</strong>
            ${n.body ? `<span class="small">${esc(n.body)}</span>` : ''}
            <span class="muted small">${timeAgo(n.created_at)}</span>
          </div>
        </div>`).join('');
    }
    panel.innerHTML = `<div class="popover-head"><strong>Notifications</strong><button type="button" class="link-btn" data-act="close">Close</button></div>${body}`;
  }

  async function load() {
    try {
      const res = await api('/api/notifications');
      s.items = res.items;
      s.unread = res.unread;
    } catch { /* keep what we have */ }
    renderBadge();
    if (!panel.hidden) renderPanel();
  }

  function close() {
    if (panel.hidden) return;
    panel.hidden = true;
    // They have now been seen.
    const now = new Date().toISOString();
    s.items.forEach(n => { n.read_at = n.read_at || now; });
  }

  async function open() {
    renderPanel();
    panel.hidden = false;
    if (s.unread) {
      s.unread = 0;
      renderBadge();
      try { await api('/api/notifications/read', { method: 'POST' }); } catch { /* retried on next open */ }
    }
  }

  button.addEventListener('click', e => {
    e.stopPropagation();
    if (panel.hidden) open(); else close();
  });
  panel.addEventListener('click', e => {
    e.stopPropagation();
    if (e.target.closest('[data-act=close]')) close();
  });
  document.addEventListener('click', close);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });

  const off = sock.on(msg => {
    if (msg.type === 'open') load();
    if (msg.type !== 'notification' || !user.notifications) return;
    const n = msg.notification;
    s.items = [n, ...s.items].slice(0, 30);
    if (panel.hidden) {
      s.unread += 1;
    } else {
      renderPanel();
      api('/api/notifications/read', { method: 'POST' }).catch(() => {});
    }
    renderBadge();
    toast(n.body ? `${n.title}: ${n.body}` : n.title, 'success');
    if (navigator.vibrate) navigator.vibrate(80);
  });

  return {
    refresh: load,
    destroy() { off(); document.removeEventListener('click', close); panel.remove(); button.remove(); },
  };
}
