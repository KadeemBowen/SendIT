/** Theme is 'system', 'light' or 'dark'. Saved on the account and cached locally so it applies before login. */
export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'light' || theme === 'dark') root.dataset.theme = theme;
  else delete root.dataset.theme;
  try { localStorage.setItem('theme', theme); } catch { /* storage unavailable */ }
  const meta = document.querySelector('meta[name=theme-color]');
  if (meta) meta.content = getComputedStyle(root).getPropertyValue('--topbar-bg').trim();
}
