import { api, auth, openSocket } from './api.js';
import { esc, toast } from './ui.js';
import { renderCustomer } from './customer.js';
import { renderRider } from './rider.js';
import { renderAdmin } from './admin.js';
import { initNotifications } from './notifications.js';
import { openProfile } from './profile.js';
import { applyTheme } from './theme.js';

const app = document.getElementById('app');
const whoami = document.getElementById('whoami');
const overlays = document.getElementById('overlays');
let cfg;

async function boot() {
  cfg = await api('/api/config');
  document.title = cfg.app_name;
  if (auth.token) {
    try {
      return start(await api('/api/me'));
    } catch {
      auth.clear();
    }
  }
  renderAuth('login');
}

function start(user) {
  const sock = openSocket();
  applyTheme(user.theme);
  whoami.innerHTML = '';
  const bell = initNotifications(whoami, overlays, sock, user);

  const profileBtn = document.createElement('button');
  profileBtn.type = 'button';
  profileBtn.className = 'top-btn';
  profileBtn.setAttribute('aria-label', 'Your profile');
  profileBtn.innerHTML = `<span class="avatar-sm">${esc(user.name.charAt(0).toUpperCase())}</span><span class="who-name">${esc(user.name.split(' ')[0])}</span>`;
  whoami.appendChild(profileBtn);

  async function logout() {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* already logged out */ }
    sock.close();
    auth.clear();
    location.reload();
  }
  profileBtn.onclick = () => openProfile(overlays, user, { onChange: bell.refresh, onLogout: logout });

  if (user.role === 'admin') renderAdmin(app, user, sock, cfg);
  else if (user.role === 'rider') renderRider(app, user, sock, cfg);
  else renderCustomer(app, user, sock, cfg);
}

function renderAuth(mode) {
  const register = mode === 'register';
  whoami.innerHTML = '';
  app.innerHTML = `
    <div class="auth-wrap">
      <div class="card auth">
        <img class="auth-logo" src="/static/logo.png" alt="${esc(cfg.app_name)}">
        <p class="muted center">Deliveries and errands, tracked live.</p>
        <div class="tabs" role="tablist">
          <button type="button" data-mode="login" class="${register ? '' : 'active'}">Log in</button>
          <button type="button" data-mode="register" class="${register ? 'active' : ''}">Create account</button>
        </div>
        <form id="auth-form" class="stack">
          ${register ? `
            <div class="role-pick">
              <label><input type="radio" name="role" value="customer" checked><span>I need deliveries</span></label>
              <label><input type="radio" name="role" value="rider"><span>I'm a rider</span></label>
            </div>
            <input name="name" placeholder="Full name" required minlength="2" maxlength="80" autocomplete="name">
            <input name="phone" placeholder="Phone number" required minlength="5" maxlength="30" inputmode="tel" autocomplete="tel">` : ''}
          <input name="email" type="email" placeholder="Email" required autocomplete="email">
          <input name="password" type="password" placeholder="Password" required minlength="6"
                 autocomplete="${register ? 'new-password' : 'current-password'}">
          ${register ? `
            <div id="rider-fields" class="stack" hidden>
              <input name="vehicle" placeholder="Vehicle (e.g. Red Honda motorbike)" maxlength="80">
              <input name="plate" placeholder="Plate number" maxlength="20">
            </div>` : ''}
          <p class="error" id="auth-error" hidden></p>
          <button class="btn btn-primary btn-block">${register ? 'Create account' : 'Log in'}</button>
        </form>
        ${cfg.demo ? `<p class="muted small demo-hint">Demo: <b>customer@demo.gy</b>, <b>rider@demo.gy</b> or <b>admin@demo.gy</b>, password <b>demo123</b></p>` : ''}
      </div>
    </div>`;

  app.querySelectorAll('[data-mode]').forEach(b => { b.onclick = () => renderAuth(b.dataset.mode); });

  const form = document.getElementById('auth-form');
  const riderFields = document.getElementById('rider-fields');
  form.querySelectorAll('input[name=role]').forEach(r => {
    r.onchange = () => {
      const isRider = form.role.value === 'rider';
      riderFields.hidden = !isRider;
      form.vehicle.required = isRider;
    };
  });

  form.onsubmit = async e => {
    e.preventDefault();
    const errEl = document.getElementById('auth-error');
    const button = form.querySelector('button.btn-primary');
    const data = Object.fromEntries(new FormData(form));
    errEl.hidden = true;
    button.disabled = true;
    try {
      const res = await api(register ? '/api/auth/register' : '/api/auth/login', { method: 'POST', body: data });
      auth.set(res.token);
      start(res.user);
      if (register) toast(`Welcome, ${res.user.name}!`, 'success');
    } catch (err) {
      errEl.textContent = err.message;
      errEl.hidden = false;
      button.disabled = false;
    }
  };
}

boot().catch(err => {
  app.innerHTML = `<div class="auth-wrap"><div class="card"><p class="error">Couldn't reach the server: ${esc(err.message)}</p></div></div>`;
});
