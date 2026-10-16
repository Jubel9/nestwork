// Quick search (⌘K, or Ctrl+K off a Mac): one box for every project, to-do, page, setting and common
// action, like Spotlight. Picking a result goes to that exact place: a to-do opens in its popup on its
// project's board, a setting opens its Settings tab and scrolls to it. Uses app.js, hud.js and chat.js helpers.

const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const SPOT_KEY = IS_MAC ? '⌘K' : 'Ctrl K';
const spot = { el: null, items: [], results: [], sel: 0, loading: null };

// [label, tab, text to find in that tab, extra words to match]
const SPOT_SETTINGS = [
  ['About you', 'agent', 'About you', 'name timezone profile me'],
  ['Your agent: name and personality', 'agent', 'Your agent', 'persona character model'],
  ['Reminder style', 'agent', 'Reminders', 'notify written quick'],
  ['What the agent can use', 'agent', 'What the agent can use', 'tools pictures selfies'],
  ['Agent appearance photos', 'agent', 'Appearance', 'face body workspace analyse'],
  ['Wardrobe', 'agent', 'Wardrobe', 'clothes outfit'],
  ['Connected apps: Google Calendar, Gmail', 'agent', 'Connected apps', 'connectors google calendar gmail drive email'],
  ['Pictures', 'agent', 'Pictures', 'drawings selfies images'],
  ['Memory files', 'memory', '', 'claude.md memory.md notes files'],
  ['AI agent access', 'access', 'Read only', 'permissions ask first full access mcp'],
  ['Let agents delete blocks', 'access', 'Let agents delete', 'delete permission'],
  ['Proposed changes', 'access', 'Proposed changes', 'approve reject pending agent changes'],
  ['Connect other Claude apps', 'access', 'Connect other Claude apps', 'mcp claude code desktop'],
  ['Focus timer lengths', 'timer', 'Lengths', 'pomodoro focus break long break minutes'],
  ['Focus timer sounds', 'timer', 'Sounds', 'chime bell volume alarm'],
  ['Focus timer notifications', 'timer', 'Notifications', 'desktop notification'],
  ['Theme: auto, light or dark', 'appearance', 'Theme', 'dark mode light mode colors'],
  ['How it works', 'help', '', 'help guide tutorial rules bubbles'],
  ['Keyboard shortcuts', 'shortcuts', '', 'keys keyboard hotkeys help cheat sheet'],
  ['Trash: restore deleted projects and to-dos', 'trash', 'Trash', 'deleted undo recover bin'],
  ['Use Nestwork on your phone', 'phone', 'Use Nestwork on your phone', 'phone iphone android mobile tailscale pair qr'],
  ['Backups', 'trash', 'Backups', 'backup restore daily automatic'],
  ['Notification sounds', 'sounds', '', 'sound notification alarm ringtone mp3 wake up sleep volume mute'],
  ['Danger zone: erase and start over', 'danger', '', 'reset erase delete everything'],
  ['Export everything (JSON)', 'trash', 'Export', 'download export json backup move'],
];
const SPOT_PAGES = [
  ['HUD', '#/', 'home dashboard clock timer today usage'],
  ['Focus', '#/focus', 'stats productivity sessions pomodoro history daily goal'],
  ['Weekly review', '#/review', 'week reflect retrospective plan look back done slipped'],
  ['Workflow', '#/projects', 'projects boards all projects'],
  ['Calendar', '#/calendar', 'calendar schedule day week month dates deadlines due plan agenda'],
  ['Agent’s Work', '#/work', 'agent scheduled tasks cron recurring automatic brief morning jobs routines'],
];
function spotActions() {
  const onProject = /^#\/b\/\d+/.test(location.hash) && state.data;
  return [
    ['New project', 'create add start template', () => newProjectModal()],
    ...(onProject ? [[`New to-do in “${state.data.block.title}”`, 'create add task', () => newTodoModal()]] : []),
    [hud.timer.endsAt ? 'Pause the focus timer' : 'Start the focus timer', 'pomodoro focus timer', () => { if (!onHudPage()) location.hash = '#/'; timerToggle(); }],
    ['Open the chat', 'agent talk ask message', () => setChatOpen(true)],
    ...(conn.v?.undo ? [[`Undo: ${conn.v.undo.label}`, 'undo back revert mistake', undoLast]] : []),
    ...['auto', 'light', 'dark'].filter(t => t !== theme).map(t => [`Theme: ${t === 'auto' ? 'follow your computer' : t}`, 'appearance mode colors', () => setTheme(t)]),
  ];
}

// ---------------------------------------------------------------- what can be found
async function spotIndex() {
  const [projects, today] = await Promise.all([api('GET', '/api/projects'), api('GET', '/api/today').catch(() => null)]);
  const notes = new Map((today?.groups || []).flatMap(g => g.items).map(i => [i.id, i.notes || '']));
  const items = [
    ...SPOT_PAGES.map(([title, hash, words]) => ({ kind: 'page', title, sub: 'Page', words, go: () => { location.hash = hash; } })),
    ...SPOT_SETTINGS.map(([title, tab, find, words]) => ({ kind: 'setting', title, sub: 'Settings', words, go: () => spotSetting(tab, find) })),
    ...SHORTCUTS.flatMap(([section, where, rows]) => rows.map(([keys, what, words]) => ({
      kind: 'shortcut', title: what, sub: `${keys.join(kbdSep(keys))} · ${section}${where ? ', ' + where : ''}`,
      words: `${words} shortcut key keyboard ${section}`, go: () => spotSetting('shortcuts', what) }))),
  ];
  for (const p of projects) {
    registerStages(p.stages);
    items.push({ kind: 'project', title: p.title, sub: `Project · ${pct(p.progress)}% done · ${p.leaves_done}/${p.leaves_total} tasks`, go: () => { location.hash = `#/b/${p.id}`; } });
    const walk = (n, path) => n.children.forEach(c => {
      items.push({ kind: c.children.length ? 'group' : 'todo', title: c.title, done: c.done,
        sub: `${[p.title, ...path].join(' › ')} · ${stageLabel(c.status)}${c.tags.length ? ' · ' + c.tags.map(t => '#' + t).join(' ') : ''}`,
        words: [notes.get(c.id) || '', ...c.tags.map(t => '#' + t + ' ' + t), ['', 'low priority', 'medium priority', 'high priority urgent'][c.priority] || ''].join(' '),
        go: () => spotOpenBlock(p.id, c.id) });
      walk(c, [...path, c.title]);
    });
    walk(p.tree, []);
  }
  return items;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
async function spotOpenBlock(projectId, id) {
  if (state.data?.block?.id !== projectId) {
    location.hash = `#/b/${projectId}`;
    for (let i = 0; i < 40 && state.data?.block?.id !== projectId; i++) await sleep(75);
  }
  openBlockPopup(id);
}
async function spotSetting(tab, find) {
  closeAllModals();
  await settingsModal(tab);
  if (!find) return;
  for (let i = 0; i < 25; i++) {   // some sections load a moment later
    const pane = modalRoot.querySelector(`[data-pane="${tab}"]`);
    const all = pane ? [...pane.querySelectorAll('h3, summary, label, button, [data-find]')] : [];
    const hit = all.find(e => e.textContent.trim().includes(find));
    if (hit) {
      const box = hit.closest('section, details, label') || hit;
      box.scrollIntoView({ block: 'center', behavior: 'smooth' });
      animate(box, 'land');
      return;
    }
    await sleep(80);
  }
}

// ---------------------------------------------------------------- matching
// Every word typed must appear (in the name, where it is, or its description); names that start with it rank first.
function spotScore(words, item) {
  const title = item.title.toLowerCase(), rest = `${item.sub} ${item.words || ''}`.toLowerCase();
  let score = 0;
  for (const w of words) {
    const i = title.indexOf(w);
    if (i === 0) score += 100;
    else if (i > 0 && /[^a-z0-9]/.test(title[i - 1])) score += 70;
    else if (i > 0) score += 45;
    else if (rest.includes(w)) score += 15;
    else return 0;
  }
  return score + { page: 6, project: 5, action: 4, group: 3, todo: 2, setting: 1, shortcut: 1 }[item.kind] - (item.done ? 8 : 0);
}
function spotMark(text, words) {
  let html = esc(text);
  for (const w of words.filter(Boolean).sort((a, b) => b.length - a.length)) {
    const re = new RegExp(`(${esc(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'i');
    if (re.test(html)) { html = html.replace(re, '<mark class="spot-hit">$1</mark>'); }
  }
  return html;
}

// ---------------------------------------------------------------- the box
const SPOT_GROUPS = [['action', 'Actions'], ['page', 'Pages'], ['project', 'Projects'], ['group', 'To-dos with to-dos inside'], ['todo', 'To-dos'], ['setting', 'Settings'], ['shortcut', 'Keyboard shortcuts']];
const SPOT_ICON = { action: 'bolt', page: 'chart', project: 'folder', group: 'archive', todo: 'note', setting: 'cog', shortcut: 'help' };

function spotBuild() {
  document.body.insertAdjacentHTML('beforeend', `
    <div id="spot" class="hidden fixed inset-0 z-[80] bg-black/50 backdrop-blur-sm items-start justify-center px-4 pt-[12vh]">
      <div role="dialog" aria-modal="true" aria-label="Quick search" class="spot-box relative w-full max-w-xl bg-white shadow-2xl">
        <div class="flex items-center gap-3 px-4 border-b border-lavender-200">
          <span class="font-mono text-lavender-700 text-lg" aria-hidden="true">›</span>
          <input id="spot-q" type="text" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-controls="spot-list" aria-autocomplete="list"
            placeholder="Search projects, to-dos, pages, settings…" class="flex-1 min-w-0 bg-transparent py-4 text-lg text-slate-900 placeholder-slate-400 focus:outline-none">
          <kbd class="hud-kbd shrink-0">esc</kbd>
        </div>
        <ul id="spot-list" role="listbox" class="max-h-[56vh] overflow-y-auto scroll-thin py-2"></ul>
        <p class="px-4 py-2 border-t border-lavender-100 text-[11px] text-slate-400 flex flex-wrap gap-x-4"><span><kbd class="hud-kbd">↑</kbd> <kbd class="hud-kbd">↓</kbd> move</span><span><kbd class="hud-kbd">↵</kbd> open</span><span><kbd class="hud-kbd">${SPOT_KEY}</kbd> open or close</span></p>
      </div>
    </div>`);
  spot.el = document.getElementById('spot');
  const q = document.getElementById('spot-q');
  q.addEventListener('input', () => { spot.sel = 0; spotRender(); });
  q.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const n = spot.results.length;
      if (n) { spot.sel = (spot.sel + (e.key === 'ArrowDown' ? 1 : -1) + n) % n; spotPaint(); }
    } else if (e.key === 'Enter') { e.preventDefault(); spotPick(spot.sel); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); spotClose(); }
  });
  spot.el.addEventListener('mousedown', e => { if (e.target === spot.el) spotClose(); });
  document.getElementById('spot-list').addEventListener('click', e => {
    const li = e.target.closest('[data-spot]');
    if (li) spotPick(Number(li.dataset.spot));
  });
  document.getElementById('spot-list').addEventListener('mousemove', e => {
    const li = e.target.closest('[data-spot]');
    if (li && Number(li.dataset.spot) !== spot.sel) { spot.sel = Number(li.dataset.spot); spotPaint(); }
  });
}

function spotRender() {
  const list = document.getElementById('spot-list');
  const raw = document.getElementById('spot-q').value.trim().toLowerCase();
  const words = raw.split(/\s+/).filter(Boolean);
  const actions = spotActions().map(([title, w, run]) => ({ kind: 'action', title, sub: 'Action', words: w, go: run }));
  const pool = [...actions, ...spot.items];
  let found;
  if (!words.length) found = pool.filter(i => ['action', 'page', 'project'].includes(i.kind));
  else found = pool.map(i => [spotScore(words, i), i]).filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]).map(([, i]) => i);
  // Grouped like Spotlight, best match's group first.
  const order = [...new Set([...found.slice(0, 1).map(i => i.kind), ...SPOT_GROUPS.map(([k]) => k)])];
  spot.results = order.flatMap(k => found.filter(i => i.kind === k).slice(0, words.length ? 8 : 12));
  if (spot.loading && !spot.items.length && words.length) {
    list.innerHTML = '<li class="px-4 py-6 text-center text-sm text-slate-400">Loading…</li>';
    return;
  }
  if (!spot.results.length) {
    list.innerHTML = `<li class="px-4 py-6 text-center text-sm text-slate-400">Nothing matches “${esc(raw)}”.</li>`;
    return;
  }
  let html = '', idx = 0, last = null;
  for (const item of spot.results) {
    if (item.kind !== last) {
      html += `<li class="px-4 pt-2 pb-1 hud-label" role="presentation">${SPOT_GROUPS.find(([k]) => k === item.kind)[1]}</li>`;
      last = item.kind;
    }
    html += `<li id="spot-${idx}" data-spot="${idx}" role="option" class="spot-row mx-2 flex items-center gap-3 px-3 py-2 cursor-pointer">
      <span class="shrink-0 text-lavender-700">${ico(SPOT_ICON[item.kind])}</span>
      <span class="min-w-0 flex-1">
        <span class="block truncate font-semibold ${item.done ? 'text-slate-400 line-through' : 'text-slate-800'}">${spotMark(item.title, words)}</span>
        <span class="block truncate text-xs text-slate-400">${esc(item.sub)}</span>
      </span>
      <span class="spot-enter shrink-0 hud-kbd">↵</span>
    </li>`;
    idx++;
  }
  list.innerHTML = html;
  spotPaint();
}
function spotPaint() {
  spot.el.querySelectorAll('[data-spot]').forEach(li => {
    const on = Number(li.dataset.spot) === spot.sel;
    li.classList.toggle('is-sel', on);
    li.setAttribute('aria-selected', on);
    if (on) li.scrollIntoView({ block: 'nearest' });
  });
  document.getElementById('spot-q').setAttribute('aria-activedescendant', `spot-${spot.sel}`);
}

async function spotOpen() {
  if (!spot.el) spotBuild();
  spot.el.classList.remove('hidden');
  spot.el.classList.add('flex');
  animate(spot.el, 'fade-in');
  animate(spot.el.querySelector('.spot-box'), 'zoom-in');
  const q = document.getElementById('spot-q');
  q.value = '';
  spot.sel = 0;
  q.focus();
  spotRender();
  spot.loading = spotIndex().then(items => { spot.items = items; }).catch(e => toast(e.message, 'error')).finally(() => { spot.loading = null; });
  await spot.loading;
  if (!spot.el.classList.contains('hidden')) spotRender();
}
function spotClose() {
  if (!spot.el || spot.el.classList.contains('hidden')) return;
  animate(spot.el, 'fade-out');
  setTimeout(() => { spot.el.classList.add('hidden'); spot.el.classList.remove('flex'); }, 170);
}
function spotPick(i) {
  const item = spot.results[i];
  if (!item) return;
  spotClose();
  item.go();
}

document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (location.hash.startsWith('#/awaken')) return;
    spot.el && !spot.el.classList.contains('hidden') ? spotClose() : spotOpen();
  }
}, true);
