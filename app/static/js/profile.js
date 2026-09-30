import { api } from './api.js';
import { applyTheme } from './theme.js';
import { esc, toast } from './ui.js';

const THEMES = [['system', 'System'], ['light', 'Light'], ['dark', 'Dark']];
const ROLES = { customer: 'Customer', rider: 'Rider', admin: 'Admin' };

/** Profile dialog: account details, theme, notifications, log out. Mutates `user` when settings change. */
export function openProfile(overlays, user, { onChange, onLogout }) {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  overlays.appendChild(backdrop);

  function render() {
    backdrop.innerHTML = `
      <div class="card modal" role="dialog" aria-modal="true" aria-label="Your profile">
        <div class="card-head"><h2>Profile</h2><button type="button" class="link-btn" data-act="close">Close</button></div>
        <div class="profile-id">
          <div class="avatar">${esc(user.name.charAt(0).toUpperCase())}</div>
          <div>
            <strong>${esc(user.name)}</strong> <span class="role-badge">${ROLES[user.role]}</span>
            <div class="muted small">${esc(user.email)}</div>
            <div class="muted small">${esc(user.phone)}${user.vehicle ? ` · ${esc(user.vehicle)}${user.plate ? ` (${esc(user.plate)})` : ''}` : ''}</div>
          </div>
        </div>

        <div class="stack">
          <div><strong>Theme</strong><div class="muted small">System follows your phone or computer setting.</div></div>
          <div class="segmented" role="group" aria-label="Theme">
            ${THEMES.map(([k, label]) => `<button type="button" data-theme-choice="${k}" class="${user.theme === k ? 'active' : ''}" aria-pressed="${user.theme === k}">${label}</button>`).join('')}
          </div>
        </div>

        <div class="setting">
          <div><strong>Notifications</strong><span class="muted small">Alerts about orders, payments and your account.</span></div>
          <label class="switch" title="Notifications on / off">
            <input type="checkbox" id="notify-toggle" ${user.notifications ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
        </div>

        <button type="button" class="btn btn-secondary btn-block danger-text" data-act="logout">Log out</button>
      </div>`;
  }

  async function save(changes) {
    const before = { theme: user.theme, notifications: user.notifications };
    Object.assign(user, changes);
    if (changes.theme) applyTheme(user.theme);
    render();
    onChange();
    try {
      Object.assign(user, await api('/api/me', { method: 'PATCH', body: changes }));
    } catch (err) {
      Object.assign(user, before);
      applyTheme(user.theme);
      render();
      onChange();
      toast(err.message, 'error');
    }
  }

  function close() {
    document.removeEventListener('keydown', onKey);
    backdrop.remove();
  }
  const onKey = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);

  backdrop.addEventListener('click', e => {
    if (e.target === backdrop || e.target.closest('[data-act=close]')) return close();
    const theme = e.target.closest('[data-theme-choice]');
    if (theme && theme.dataset.themeChoice !== user.theme) save({ theme: theme.dataset.themeChoice });
    if (e.target.closest('[data-act=logout]')) onLogout();
  });
  backdrop.addEventListener('change', e => {
    if (e.target.id === 'notify-toggle') save({ notifications: e.target.checked });
  });

  render();
  backdrop.querySelector('[data-act=close]').focus();
}
