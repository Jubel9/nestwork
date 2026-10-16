// The phone interface (screens narrower than 768 px; the desktop is untouched): a bottom tab bar (Today, Timer,
// + quick add, Projects, Chat), pop-ups as bottom sheets you swipe down to close, swipe gestures on to-dos and
// cards, long-press for the menu, pull to refresh, swipe from the left edge to go back (Home Screen app), a
// full-screen focus timer that keeps the screen on (#/timer), a full-height chat that stays above the keyboard,
// and a "can't reach your Mac" banner. Uses app.js, hud.js, editor.js, chat.js and contextmenu.js.

const MOBILE_MQ = matchMedia('(max-width: 767px)');
const mobile = { on: false, lastLongPress: 0 };
const isMobile = () => mobile.on;

function syncMobile() {
  mobile.on = MOBILE_MQ.matches;
  document.body.classList.toggle('is-mobile', mobile.on);
  document.body.classList.toggle('is-standalone', isInstalledApp);
}
MOBILE_MQ.addEventListener('change', syncMobile);
syncMobile();

// ---------------------------------------------------------------- bottom tab bar
const TABS = [
  ['today', '#/', 'home', 'Today'], ['timer', '#/timer', 'timer', 'Timer'], ['add', null, null, 'Add'],
  ['projects', '#/projects', 'folder', 'Projects'], ['chat', null, 'chat', 'Chat'],
];
function tabbarHTML() {
  return `<nav id="tabbar" class="mobile-only" aria-label="Main">
    ${TABS.map(([k, href, icon, label]) => k === 'add'
      ? `<button data-tab-add class="tab-add" aria-label="Add a to-do"><span>+</span></button>`
      : `<${href ? `a href="${href}"` : 'button'} data-tabbar="${k}" class="tab"><span class="tab-ico">${ico(icon)}</span><span class="tab-label">${label}</span>${k === 'chat' ? '<span data-tab-badge class="tab-badge hidden"></span>' : ''}</${href ? 'a' : 'button'}>`).join('')}
  </nav>`;
}
document.body.insertAdjacentHTML('beforeend', tabbarHTML());
document.body.insertAdjacentHTML('beforeend', '<div id="offline-banner" class="hidden" role="status">Can’t reach your Mac. Showing what you saw last.</div><div id="ptr" aria-hidden="true">↻</div>');
const tabbar = document.getElementById('tabbar');

function syncTabbar() {
  const h = location.hash || '#/';
  const current = h.startsWith('#/timer') ? 'timer' : h.startsWith('#/projects') || h.startsWith('#/b/') ? 'projects' : h === '#/' ? 'today' : '';
  tabbar.querySelectorAll('[data-tabbar]').forEach(t => t.classList.toggle('is-on', t.dataset.tabbar === current || (t.dataset.tabbar === 'chat' && document.body.classList.contains('chat-open'))));
  const badge = document.getElementById('chat-badge'), mine = tabbar.querySelector('[data-tab-badge]');
  if (badge && mine) { mine.textContent = badge.textContent; mine.classList.toggle('hidden', badge.classList.contains('hidden')); }
}
window.addEventListener('hashchange', syncTabbar);
new MutationObserver(syncTabbar).observe(document.body, { attributes: true, attributeFilter: ['class'] });
const chatBadge = document.getElementById('chat-badge');
if (chatBadge) new MutationObserver(syncTabbar).observe(chatBadge, { attributes: true, childList: true, characterData: true, subtree: true });
syncTabbar();
tabbar.querySelector('[data-tabbar="chat"]').onclick = () => setChatOpen(!document.body.classList.contains('chat-open'));
tabbar.querySelector('[data-tab-add]').onclick = quickAddSheet;

// The + button: a to-do in one of your projects, or ask your agent to file it.
async function quickAddSheet() {
  const projects = hud.projects?.length ? hud.projects : await api('GET', '/api/projects').catch(() => []);
  if (!projects.length) return newProjectModal();
  const last = localGet('nestwork.hudProject');
  openModal(`
    <div class="flex items-center justify-between mb-3"><h2 class="text-lg font-semibold">Add a to-do</h2>
      <button data-close class="p-1 rounded text-slate-400" aria-label="Close">✕</button></div>
    <form data-qa class="space-y-3">
      <input name="title" required maxlength="200" autocomplete="off" placeholder="What needs doing?" class="w-full rounded-lg border border-slate-300 px-3 py-2.5">
      <select name="project" class="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5">${projects.map(p => `<option value="${p.id}" ${String(p.id) === last ? 'selected' : ''}>${esc(p.title)}</option>`).join('')}</select>
      <button class="w-full rounded-full bg-lavender-700 text-white font-bold py-3">Add</button>
      <button type="button" data-qa-agent class="w-full rounded-full border border-lilac-300 text-lilac-700 font-semibold py-2.5">✦ Tell my agent instead</button>
      <p class="text-xs text-slate-500">Your agent can pick the project, the date and the priority from what you write, e.g. “email my professor about the extension by Friday”.</p>
    </form>`, root => {
    const f = root.querySelector('[data-qa]');
    setTimeout(() => f.title.focus(), 250);
    f.onsubmit = async e => {
      e.preventDefault();
      localSet('nestwork.hudProject', f.project.value);
      if (await attempt(() => api('POST', '/api/blocks', { title: f.title.value.trim(), parent_id: Number(f.project.value) }))) {
        toast(`Added “${f.title.value.trim()}”.`, 'success'); closeModal(); refresh();
      }
    };
    root.querySelector('[data-qa-agent]').onclick = () => {
      const text = f.title.value.trim();
      closeModal();
      setChatOpen(true, { focus: !text });
      if (text) sendMessage(`Add this as a to-do where it fits best (pick the project, and a date or priority if I said one): ${text}`);
    };
  });
}

// ---------------------------------------------------------------- full-screen focus timer (#/timer)
let wakeLock = null;
async function renderTimerScreen() {
  state.data = null;
  const [projects, today] = await Promise.all([api('GET', '/api/projects'), api('GET', '/api/today')]);
  projects.forEach(p => registerStages(p.stages));
  hud.projects = projects;
  hud.lastToday = today;
  api('GET', '/api/focus/labels').then(r => { hud.labels = r.labels; }).catch(() => {});
  document.title = 'Focus timer · Nestwork';
  app.innerHTML = `<div class="hud timer-screen">${timerPanel()}</div>`;
  bindTimer(s => app.querySelector(s));
  tickHud();
  syncWakeLock();
}
// Keep the screen on while the timer runs on this screen.
async function syncWakeLock() {
  const want = location.hash.startsWith('#/timer') && (hud.timer.endsAt || hud.timer.swFrom) && !document.hidden;
  try {
    if (want && !wakeLock && 'wakeLock' in navigator) { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener('release', () => { wakeLock = null; }); }
    else if (!want && wakeLock) { await wakeLock.release(); wakeLock = null; }
  } catch { wakeLock = null; }
}
document.addEventListener('visibilitychange', syncWakeLock);
setInterval(syncWakeLock, 5000);

// ---------------------------------------------------------------- bottom sheets
// On a phone every pop-up and side panel slides up from the bottom; drag its handle (or the top of it) down to close.
new MutationObserver(() => {
  if (!isMobile()) return;
  modalRoot.querySelectorAll(':scope > [data-backdrop]:not([data-sheet])').forEach(layer => {
    layer.dataset.sheet = '';
    const box = layer.firstElementChild;
    box.insertAdjacentHTML('afterbegin', '<div class="sheet-handle" aria-hidden="true"></div>');
    let startY = null, dy = 0;
    box.addEventListener('touchstart', e => {
      const fromHandle = e.target.closest('.sheet-handle');
      if (!fromHandle && box.scrollTop > 0) return;
      if (e.target.closest('input, textarea, select, [contenteditable]')) return;
      startY = e.touches[0].clientY; dy = 0;
    }, { passive: true });
    box.addEventListener('touchmove', e => {
      if (startY === null) return;
      dy = Math.max(0, e.touches[0].clientY - startY);
      if (dy > 0) { box.style.transform = `translateY(${dy}px)`; box.style.transition = 'none'; }
    }, { passive: true });
    box.addEventListener('touchend', () => {
      if (startY === null) return;
      box.style.transition = '';
      if (dy > 110) closeModal(); else box.style.transform = '';
      startY = null;
    });
  });
}).observe(modalRoot, { childList: true });

// ---------------------------------------------------------------- swipes, long-press, pull to refresh, edge back
// Swipe a to-do (Today, plan) right to finish it, left to move it to tomorrow; swipe a board card right or left
// to move it to the next or previous phase. Long-press anything for its menu.
const SWIPE_AT = 90;
function swipeTarget(el) {
  if (!isMobile() || el.closest('input, textarea, select, button, a[href^="http"]')) return null;
  return el.closest('#app [data-task], #app .card[data-id]');
}
async function finishTodo(id) {
  const d = await api('GET', `/api/blocks/${id}`);
  return attempt(() => api('POST', `/api/blocks/${id}/move`, { status: d.stages[d.stages.length - 1].key }));
}
async function moveTodoTomorrow(id) {
  const t = new Date(); t.setDate(t.getDate() + 1);
  return attempt(() => api('PATCH', `/api/blocks/${id}`, { due_date: isoDay(t) }));
}
async function shiftPhase(id, step) {
  const d = await api('GET', `/api/blocks/${id}`), keys = d.stages.map(s => s.key);
  const next = keys[keys.indexOf(d.block.status) + step];
  if (!next) return toast(step > 0 ? 'Already in the last phase.' : 'Already in the first phase.', 'info');
  return attempt(() => api('POST', `/api/blocks/${id}/move`, { status: next }));
}
let touch = null;
document.addEventListener('touchstart', e => {
  if (e.touches.length !== 1) { touch = null; return; }
  const t = e.touches[0];
  touch = { x: t.clientX, y: t.clientY, el: swipeTarget(e.target), axis: null, dx: 0, dy: 0, target: e.target,
    edge: isInstalledApp && t.clientX < 22, pull: isMobile() && window.scrollY <= 0 && !modalRoot.children.length && !document.body.classList.contains('chat-open') };
  clearTimeout(mobile.pressTimer);
  if (isMobile()) mobile.pressTimer = setTimeout(() => {
    if (!touch || touch.axis) return;
    mobile.lastLongPress = Date.now();
    touch.target.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: touch.x, clientY: touch.y }));
    touch = null;
  }, 550);
}, { passive: true });
document.addEventListener('touchmove', e => {
  if (!touch) return;
  const t = e.touches[0];
  touch.dx = t.clientX - touch.x; touch.dy = t.clientY - touch.y;
  if (!touch.axis && Math.hypot(touch.dx, touch.dy) > 10) { touch.axis = Math.abs(touch.dx) > Math.abs(touch.dy) ? 'x' : 'y'; clearTimeout(mobile.pressTimer); }
  if (touch.axis === 'x' && touch.el) {
    touch.el.style.transform = `translateX(${touch.dx}px)`;
    touch.el.style.transition = 'none';
    const card = touch.el.matches('.card');
    touch.el.dataset.swipe = touch.dx > SWIPE_AT ? (card ? 'next' : 'done') : touch.dx < -SWIPE_AT ? (card ? 'back' : 'tomorrow') : '';
  }
  if (touch.axis === 'y' && touch.pull && touch.dy > 0) {
    const ptr = document.getElementById('ptr');
    ptr.style.transform = `translate(-50%, ${Math.min(touch.dy, 120) - 40}px) rotate(${touch.dy * 3}deg)`;
    ptr.classList.toggle('is-ready', touch.dy > 90);
    ptr.classList.add('is-pulling');
  }
}, { passive: true });
document.addEventListener('touchend', async () => {
  clearTimeout(mobile.pressTimer);
  const t = touch;
  touch = null;
  if (!t) return;
  const ptr = document.getElementById('ptr');
  ptr.style.transform = ''; ptr.classList.remove('is-ready', 'is-pulling');
  if (t.axis === 'y' && t.pull && t.dy > 90) { await poll(true); await refresh(); toast('Up to date.', 'info'); return; }
  if (t.edge && t.axis === 'x' && t.dx > 80) { history.back(); return; }
  if (!t.el) return;
  const el = t.el, id = Number(el.dataset.task || el.dataset.id), action = el.dataset.swipe;
  el.style.transition = ''; el.style.transform = ''; el.dataset.swipe = '';
  if (!action) return;
  const ok = action === 'done' ? await finishTodo(id) : action === 'tomorrow' ? await moveTodoTomorrow(id)
    : await shiftPhase(id, action === 'next' ? 1 : -1);
  if (ok) { toast({ done: 'Done ✓', tomorrow: 'Moved to tomorrow.', next: 'Moved forward.', back: 'Moved back.' }[action], 'success', { label: 'Undo', run: undoLast }); refresh(); }
});
// A long-press opens the menu; don't let the finger lifting also count as a tap.
document.addEventListener('click', e => { if (Date.now() - mobile.lastLongPress < 600) { e.preventDefault(); e.stopPropagation(); } }, true);

// ---------------------------------------------------------------- chat above the keyboard, offline banner
function syncViewport() {
  const vv = window.visualViewport;
  if (!vv) return;
  document.documentElement.style.setProperty('--vvh', `${vv.height}px`);
  document.documentElement.style.setProperty('--vvtop', `${vv.offsetTop}px`);
}
window.visualViewport?.addEventListener('resize', syncViewport);
window.visualViewport?.addEventListener('scroll', syncViewport);
syncViewport();

setInterval(() => document.getElementById('offline-banner').classList.toggle('hidden', conn.ok !== false), 1000);
