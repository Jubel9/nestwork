// Nestwork frontend: vanilla JS + Tailwind. Hash routes: #/ (the HUD, hud.js), #/projects (projects
// and to-do list), #/calendar (every project's dates, calendar.js), #/b/<id> (a block's nest) and #/work (Agent's Work, agentwork.js).

const app = document.getElementById('app');
const modalRoot = document.getElementById('modal-root');

// Phase colors by name (their neon values live in index.html's CSS variables). Every project picks a color per phase;
// phases are looked up by key in this registry, filled from whatever the API last returned.
const PALETTE = ['lavender', 'baby', 'peach', 'lilac', 'mint', 'butter', 'blush'];
const stageInfo = {};
function registerStages(stages) { (stages || []).forEach(s => { stageInfo[s.key] = s; }); }
const styleFor = c => ({
  color: c,
  dot: `bg-${c}-300`,
  head: `text-${c}-700`,
  pill: `bg-${c}-100 text-${c}-700`,
  column: `bg-${c}-50/80 border-${c}-100`,
  card: `border-${c}-200`,
});
const STAGE_STYLE = new Proxy({}, { get: (_, key) => styleFor(stageInfo[key]?.color || 'lavender') });
// "Work in progress" phases (role active) are where work is happening: their dots and cards show it.
const isLiveStage = key => stageInfo[key]?.role === 'active';
// The phases to-do controls work with: the open board's, or, while a to-do popup from another project is open
// (from the Calendar, the HUD, a reminder…), that project's. Set by drawPopup, cleared when the popup closes.
let popupStages = null;
const curStages = () => popupStages || state.data.stages;
const firstKey = () => curStages()[0].key;
const lastKey = () => curStages()[curStages().length - 1].key;
// Rotating accents for project cards.
const ACCENTS = ['blush', 'peach', 'butter', 'mint', 'baby', 'lilac', 'lavender'];

const state = {
  view: localGet('nestwork.view') || 'board',
  collapsed: new Set(JSON.parse(localGet('nestwork.collapsed') || '[]')),
  data: null,       // last loaded block payload
  breakdown: new Set(),
  cols: JSON.parse(localGet('nestwork.cols') || '{}'),  // stage -> 'open' | 'closed', chosen by the user
  mobileStage: null,     // stage shown on phones  // empty blocks the user chose to turn into a to-do list this session
  templates: null,
  showChain: false,      // light up the longest chain of waiting to-dos on the board
};

function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

// ---------------------------------------------------------------- utilities
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    let msg = data?.detail ?? 'Something went wrong';
    if (Array.isArray(msg)) msg = msg.map(d => d.msg).join('; ');
    else if (typeof msg === 'object') msg = msg.message ?? 'Something went wrong';
    // the status and the full detail ride along, for callers that handle a structured error (e.g. a 409 to confirm)
    throw Object.assign(new Error(msg), { status: res.status, detail: data?.detail });
  }
  return data;
}

// action: { label, run } adds a button (e.g. Undo) and keeps the toast up a little longer.
function toast(message, kind = 'info', action = null) {
  const colors = {
    info: 'bg-lavender-100 text-lavender-800 border border-lavender-200',
    error: 'bg-blush-100 text-blush-700 border border-blush-200',
    success: 'bg-mint-100 text-mint-700 border border-mint-200',
  };
  const el = document.createElement('div');
  el.className = `pointer-events-auto max-w-sm rounded-xl shadow-lg px-4 py-3 text-sm ${colors[kind]}`;
  el.textContent = message;
  const close = async () => { if (!el.isConnected) return; await animateOut([el], 'toast-out', 240); el.remove(); };
  if (action) {
    el.classList.add('flex', 'items-center', 'gap-3');
    el.innerHTML = `<span class="flex-1">${esc(message)}</span><button class="shrink-0 rounded-full border border-current px-2.5 py-0.5 text-xs font-bold hover:bg-white/60">${esc(action.label)}</button>`;
    el.querySelector('button').onclick = () => { close(); action.run(); };
  }
  document.getElementById('toasts').appendChild(el);
  animate(el, 'toast-in', document.getElementById('toasts').children.length > 1 ? 80 : 0);
  setTimeout(close, action ? 9000 : kind === 'error' ? 6000 : 4500);
}

// After a delete: say it went to the trash, with Undo.
function deletedToast(title, trashId) {
  toast(`Deleted “${title}”. It’s in the trash for 30 days.`, 'success', trashId ? { label: 'Undo', run: async () => {
    const r = await attempt(() => api('POST', `/api/trash/${trashId}/restore`));
    if (r) refresh();
  } } : null);
}

function notify(result) { (result?.notices || []).forEach(n => toast(n, n.startsWith('⚠') ? 'error' : 'success')); }

async function attempt(fn) {
  try { const r = await fn(); notify(r); return r; }
  catch (e) { toast(e.message, 'error'); return null; }
}

function pct(p) { return Math.round((p || 0) * 100); }

// ---------------------------------------------------------------- motion
// Remembers what was on screen so re-renders only animate what actually changed.
const motion = {
  page: null,          // id of the page shown ('home' or a block id)
  depth: null,         // its nesting depth, to zoom in when going deeper and out when going up
  view: null,          // which view/tab is showing on that page
  cards: new Map(),    // card id -> stage, for the current page
  bubbles: new Map(),  // bubble id -> stage
  bars: new Map(),     // progress bar key -> last width %
  entering: false,     // true while a page is drawn for the first time
};

function animate(el, name, delay = 0) {
  if (!el) return;
  const cls = `a-${name}`;
  [...el.classList].filter(c => c.startsWith('a-')).forEach(c => el.classList.remove(c));
  void el.offsetWidth;                       // restart if it was already running
  el.style.animationDelay = delay ? `${delay}ms` : '';
  el.classList.add(cls);
  const finish = () => {
    el.classList.remove(cls);
    el.style.animationDelay = '';
    el.removeEventListener('animationend', done);
  };
  function done(e) { if (e.target === el && !e.pseudoElement) finish(); }   // ignore nested elements and ::before scan lines
  el.addEventListener('animationend', done);
  setTimeout(() => { if (el.classList.contains(cls)) finish(); }, delay + 1500);   // e.g. tab in background
}

// Plays an exit animation, then resolves (so the element can be removed afterwards).
function animateOut(els, name, ms) {
  [...els].forEach(el => animate(el, name));
  return new Promise(r => setTimeout(r, els.length ? ms : 0));
}

function stagger(els, name = 'pop-in', step = 35, max = 450) {
  [...els].forEach((el, i) => animate(el, name, Math.min(i * step, max)));
}

// Progress bars grow from where they were (or from zero on a fresh page).
function animateBars(root, entering) {
  root.querySelectorAll('[data-bar]').forEach(track => {
    const fill = track.firstElementChild, key = track.dataset.bar;
    const target = parseFloat(fill.style.width) || 0;
    const from = entering ? 0 : (motion.bars.get(key) ?? target);
    motion.bars.set(key, target);
    if (from === target) return;
    fill.style.transition = 'none';
    fill.style.width = `${from}%`;
    void fill.offsetWidth;
    fill.style.transition = '';
    fill.style.width = `${target}%`;
    animate(fill, 'sheen');   // one light sweep while the value actually changes
  });
}

// Top-level bubbles cascade in on a new page; bubbles whose stage changed bounce.
function animateBubbles(root, entering) {
  root.querySelectorAll('[data-go]').forEach(el => {
    const id = Number(el.dataset.go), stage = el.dataset.stage;
    const before = motion.bubbles.get(id);
    if (!entering && before !== undefined && before !== stage) animate(el, 'land');
    motion.bubbles.set(id, stage);
  });
  if (entering) stagger([...root.querySelectorAll('[data-go]')].filter(el => !el.parentElement.closest('[data-go]')), 'pop-in', 30);
}

// New cards pop in, cards that changed stage (by you or automatically) land with a bounce.
function animateCards(root, children, entering) {
  const cards = [...root.querySelectorAll('.card')];
  if (entering) stagger(cards);
  else cards.forEach(el => {
    const id = Number(el.dataset.id), c = children.find(x => x.id === id);
    const before = motion.cards.get(id);
    if (before === undefined) animate(el, 'pop-in');
    else if (c && before !== c.status) animate(el, 'land');
  });
  motion.cards = new Map(children.map(c => [c.id, c.status]));
}

// Page-level transition: deeper = zoom in, up = zoom out, sideways = slide.
function enterPage(key, depth) {
  const entering = motion.page !== key;
  if (entering) {
    const dir = motion.depth == null ? 'fade' : depth > motion.depth ? 'deeper' : depth < motion.depth ? 'up' : 'side';
    animate(app, `page-${dir}`);
    // Titles glitch in once the wipe has started (after the HUD clock shows the real time).
    requestAnimationFrame(() => app.querySelectorAll('h1, [data-hud-time]').forEach(el => { el.dataset.text = el.textContent; animate(el, 'glitch', 160); }));
    motion.cards = new Map();
    motion.bubbles = new Map();
  }
  // Every redraw: titles carry their own text for the idle glitch (always the real title).
  requestAnimationFrame(() => app.querySelectorAll('h1').forEach(el => { el.dataset.text = el.textContent; }));
  motion.page = key;
  motion.depth = depth;
  return entering;
}

function bar(progress, cls = 'h-1.5', key = null) {
  const p = pct(progress);
  return `<div class="w-full ${cls} rounded-full bg-slate-200/60 overflow-hidden" ${key ? `data-bar="${key}"` : ''}>
    <div data-bar-fill class="${cls} rounded-full ${p === 100 ? 'bg-mint-300' : 'bg-gradient-to-r from-lavender-300 via-baby-300 to-lilac-300'}" style="width:${p}%"></div></div>`;
}

// ---------------------------------------------------------------- dropdowns
// A field with options is a real <select>; "Other…" swaps it for a text box so any value still fits.
const MODELS = [['', 'Default (Claude Code’s choice)'], ['opus', 'Opus: most capable'], ['sonnet', 'Sonnet: balanced'], ['haiku', 'Haiku: fastest, cheapest']];
const OTHER = '__other__';

function fieldOptions(f) {
  if (f.options === 'timezones') {
    try { return Intl.supportedValuesOf('timeZone'); } catch { return [Intl.DateTimeFormat().resolvedOptions().timeZone]; }
  }
  return f.options || null;
}

function fieldControl(attr, f, value, cls) {
  const options = fieldOptions(f);
  if (!options) return `<input ${attr} value="${esc(value)}" placeholder="${esc(f.hint || '')}" class="${cls}">`;
  const custom = value && !options.includes(value);
  const hint = f.options === 'timezones' ? `Your computer: ${Intl.DateTimeFormat().resolvedOptions().timeZone}` : 'Choose…';
  return `<select ${attr} class="${cls}">
      <option value="" ${value ? '' : 'selected'}>${esc(hint)}</option>
      ${options.map(o => `<option value="${esc(o)}" ${o === value ? 'selected' : ''}>${esc(o)}</option>`).join('')}
      ${custom ? `<option value="${esc(value)}" selected>${esc(value)}</option>` : ''}
      <option value="${OTHER}">Other… (type your own)</option>
    </select>`;
}

function modelSelect(id, current, cls) {
  const known = MODELS.some(([k]) => k === current);
  return `<select id="${id}" class="${cls}">
    ${MODELS.map(([k, l]) => `<option value="${k}" ${k === (current || '') ? 'selected' : ''}>${l}</option>`).join('')}
    ${known ? '' : `<option value="${esc(current)}" selected>${esc(current)}</option>`}
  </select>`;
}

// "Other…" turns the dropdown into a text box (same name, so saving code doesn't change).
document.addEventListener('change', e => {
  const sel = e.target;
  if (!(sel instanceof HTMLSelectElement) || sel.value !== OTHER) return;
  const input = document.createElement('input');
  for (const a of sel.attributes) if (a.name !== 'class') input.setAttribute(a.name, a.value);
  input.className = sel.className;
  input.placeholder = 'Type your own';
  sel.replaceWith(input);
  input.focus();
  e.stopImmediatePropagation();
}, true);

function stageLabel(key) { return stageInfo[key]?.label ?? key; }

function dueBadge(due, done, time = null) {
  if (!due) return '';
  const today = state.data?.today || new Date().toISOString().slice(0, 10);
  const overdue = !done && due < today;
  const soon = !done && !overdue && (new Date(due) - new Date(today)) / 864e5 <= 3;
  const cls = overdue ? 'bg-blush-100 text-blush-700' : soon ? 'bg-butter-100 text-butter-700' : 'bg-slate-100 text-slate-600';
  const d = new Date(due + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  return `<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ${cls}">${overdue ? 'Overdue · ' : ''}${d}${time ? ` · ${time}` : ''}</span>`;
}

// Repeating to-dos (see nestwork/repeats.py).
const REPEAT_PRESETS = [['none', 'Doesn’t repeat'], ['daily', 'Every day'], ['weekdays', 'Every weekday (Mon–Fri)'], ['weekly', 'Every week'],
  ['biweekly', 'Every 2 weeks'], ['monthly', 'Every month'], ['yearly', 'Every year'], ['custom', 'Custom…']];
const REPEAT_RULES = {
  daily: { freq: 'daily', interval: 1 }, weekdays: { freq: 'weekly', interval: 1, weekdays: [0, 1, 2, 3, 4] },
  weekly: { freq: 'weekly', interval: 1 }, biweekly: { freq: 'weekly', interval: 2 },
  monthly: { freq: 'monthly', interval: 1 }, yearly: { freq: 'yearly', interval: 1 },
};
const repeatKey = r => r ? JSON.stringify([r.freq, r.interval || 1, r.weekdays || []]) : '';
const repeatPreset = r => !r ? 'none' : Object.keys(REPEAT_RULES).find(k => repeatKey(REPEAT_RULES[k]) === repeatKey(r)) || 'custom';
function repeatBadge(c) {
  if (!c.repeat_text) return '';
  const back = c.repeat_next ? ` · back ${fmtDay(c.repeat_next.reopen)}` : '';
  return `<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs bg-lilac-50 text-lilac-700" title="${esc(c.repeat_text)}${c.repeat_next ? `: done for now, back on ${esc(c.repeat_next.reopen)}` : ''}">🔁 ${esc(c.repeat_text.replace(/^Every /, ''))}${esc(back)}</span>`;
}
const PRIORITY = [['0', 'None'], ['1', 'Low'], ['2', 'Medium'], ['3', 'High']];
const PRIORITY_STYLE = { 1: 'bg-slate-100 text-slate-500', 2: 'bg-butter-50 text-butter-700', 3: 'bg-blush-50 text-blush-700' };
function priorityBadge(c) {
  return c.priority ? `<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold ${PRIORITY_STYLE[c.priority]}" title="${PRIORITY[c.priority][1]} priority">${ico('flag')}${PRIORITY[c.priority][1]}</span>` : '';
}
function tagChips(c) {
  return (c.tags || []).map(t => `<span class="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-xs bg-lavender-50 text-lavender-700 border border-lavender-100" title="Tag">#${esc(t)}</span>`).join('');
}
function fmtDay(iso) { return new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }); }
// Durations use fmtMin from focusview.js ("2 h 05" / "25 min").
// Estimate vs focus time spent, for a single task.
function estimateBadge(c) {
  if (c.children_total || (!c.estimate_min && !c.spent_min)) return '';
  const over = c.estimate_min && c.spent_min > c.estimate_min;
  const text = c.estimate_min ? (c.spent_min ? `${fmtMin(c.spent_min)} / ${fmtMin(c.estimate_min)}` : fmtMin(c.estimate_min)) : `${fmtMin(c.spent_min)} spent`;
  return `<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ${over ? 'bg-blush-50 text-blush-700' : 'bg-slate-100 text-slate-600'}" title="${c.estimate_min ? `Estimate ${fmtMin(c.estimate_min)}` : 'No estimate'}${c.spent_min ? ` · ${fmtMin(c.spent_min)} of focus spent` : ''}">⏳ ${text}</span>`;
}
// The forecast for a block with to-dos inside (see nestwork/forecast.py).
function forecastHTML(f, compact = false) {
  if (!f || !f.open) return '';
  if (f.remaining_min == null) return compact ? '' : `<div class="rounded-xl bg-slate-50 border border-slate-200 px-3 py-2 text-slate-600">⏳ <span class="font-bold">Forecast:</span> give the to-dos estimates (Edit → ⏳ Estimate) to see when this should be done.${chainLineHTML(f)}</div>`;
  const late = f.late_days != null && f.late_days > 0;
  const when = f.finish ? `done around <b>${esc(fmtDay(f.finish))}</b>` : 'no finish date yet (no focus time in the last 14 days)';
  const due = f.late_days == null ? '' : late ? ` · <span class="text-blush-700 font-semibold">${f.late_days} day${f.late_days === 1 ? '' : 's'} after its due date</span>` : ' · <span class="text-mint-700 font-semibold">in time for its due date</span>';
  if (compact) return `<span class="text-xs ${late ? 'text-blush-700' : 'text-slate-500'}" title="${esc(fmtMin(f.remaining_min))} of work left">⏳ ${f.finish ? `≈ ${esc(fmtDay(f.finish))}` : esc(fmtMin(f.remaining_min)) + ' left'}${late ? ` (${f.late_days}d late)` : ''}</span>`;
  const notes = [];
  if (f.estimated < f.open) notes.push(`${f.estimated} of ${f.open} open to-dos estimated; the others count as ${fmtMin(f.typical_min)} each`);
  if (f.over.length) notes.push(`${f.over.length} past ${f.over.length === 1 ? 'its' : 'their'} estimate: ${f.over.map(o => esc(o.title)).join(', ')}`);
  return `<div class="rounded-xl ${late ? 'bg-blush-50 border-blush-200' : 'bg-baby-50 border-baby-100'} border px-3 py-2 text-slate-700">
    <p>⏳ <span class="font-bold">Forecast:</span> about <b>${esc(fmtMin(f.remaining_min))}</b> of work left, ${when}${due}.</p>
    <p class="text-xs text-slate-500 mt-0.5">At ${esc(fmtMin(f.pace_min_per_day))} of focus a day ${f.pace_from === 'project' ? 'on this project' : 'in total (none on this project yet)'} over the last 14 days${f.spent_min ? ` · ${esc(fmtMin(f.spent_min))} spent so far` : ''}.${notes.length ? ' ' + notes.join(' · ') + '.' : ''}</p>
    ${chainLineHTML(f)}
  </div>`;
}
// The longest line of to-dos that wait on each other, with a switch to light it up on the board.
function chainLineHTML(f) {
  if (!f.chain.length) return '';
  const size = f.chain_min ? fmtMin(f.chain_min) : `${f.chain.length} to-dos`;
  return `<p class="text-xs text-slate-500 mt-0.5 flex flex-wrap items-center gap-x-2" title="These wait on each other through rules, so they can only be done one after another: start them early.">
    <span>Longest chain: ${f.chain.map(c => `<a href="#/b/${c.id}" class="underline">${esc(c.title)}</a>`).join(' → ')} (${esc(size)})</span>
    <button data-chain-toggle class="rounded-full border border-current px-2 py-0 text-[11px] font-semibold hover:bg-white/60">${state.showChain ? 'Hide on board' : 'Show on board'}</button></p>`;
}

const ICON = {
  lock: '<svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 1a4.5 4.5 0 00-4.5 4.5V9H5a2 2 0 00-2 2v6a2 2 0 002 2h10a2 2 0 002-2v-6a2 2 0 00-2-2h-.5V5.5A4.5 4.5 0 0010 1zm3 8V5.5a3 3 0 10-6 0V9h6z" clip-rule="evenodd"/></svg>',
  nest: '<svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="2.5" y="2.5" width="15" height="15" rx="3"/><rect x="6.5" y="6.5" width="7" height="7" rx="1.5"/></svg>',
  dots: '<svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path d="M6 10a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0zm5.5 0a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0zM17 10a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z"/></svg>',
  fold: '<svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5l4 5-4 5M16 5l-4 5 4 5"/></svg>',
  caret: '<svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M7.2 14.8a.75.75 0 010-1.06L10.94 10 7.2 6.26a.75.75 0 111.06-1.06l4.27 4.27a.75.75 0 010 1.06L8.26 14.8a.75.75 0 01-1.06 0z" clip-rule="evenodd"/></svg>',
};

// ---------------------------------------------------------------- line icons instead of emoji
// Strings across the UI (and the server's notices, templates and reminder messages) mark things with
// emoji. On screen every known one is swapped for a line icon drawn in the current text color, so the
// strings and the API stay as they are. 🎉 is simply dropped. Agent-written chat text is left alone
// except for these markers.
const LINE_ICONS = {
  warn: '<path d="M8 1.8 15 14.2H1z"/><path d="M8 6.2v3.8M8 11.9v.2"/>',
  bolt: '<path d="M9.2 1.2 2.8 9h4.6l-.6 5.8L13.2 7H8.6z"/>',
  bell: '<path d="M3.8 11.2V7a4.2 4.2 0 0 1 8.4 0v4.2l1.5 1.4H2.3zM6.4 14.6h3.2"/>',
  note: '<path d="M3 1.5h7l3 3v10H3z"/><path d="M5.5 7h5M5.5 9.5h5M5.5 12h3"/>',
  timer: '<circle cx="8" cy="9" r="5.5"/><path d="M8 9V6M6 1.5h4M12.6 4l1-1"/>',
  cog: '<circle cx="8" cy="8" r="2.3"/><path d="M8 1.2v2.2M8 12.6v2.2M1.2 8h2.2M12.6 8h2.2M3.2 3.2l1.6 1.6M11.2 11.2l1.6 1.6M3.2 12.8l1.6-1.6M11.2 4.8l1.6-1.6"/>',
  search: '<circle cx="7" cy="7" r="4.6"/><path d="M10.4 10.4l4.2 4.2"/>',
  user: '<circle cx="8" cy="5" r="3"/><path d="M2.5 14.8c.5-3 2.7-4.6 5.5-4.6s5 1.6 5.5 4.6"/>',
  chip: '<rect x="4" y="4" width="8" height="8"/><rect x="6.5" y="6.5" width="3" height="3"/><path d="M6 1.2V4M10 1.2V4M6 12v2.8M10 12v2.8M1.2 6H4M1.2 10H4M12 6h2.8M12 10h2.8"/>',
  bot: '<rect x="2.5" y="5" width="11" height="8.5"/><path d="M8 1.8V5M5.6 8.6v1.2M10.4 8.6v1.2M1 8.2v2.6M15 8.2v2.6"/>',
  palette: '<path d="M8 1.5a6.5 6.5 0 1 0 0 13c1 0 1.5-.7 1.2-1.6-.4-1 .3-1.9 1.3-1.9h1.5a2.5 2.5 0 0 0 2.5-2.5C14.5 4.4 11.6 1.5 8 1.5z"/><path d="M5 7.2v.1M8 4.6v.1M11 7.2v.1"/>',
  tools: '<rect x="1.5" y="5" width="13" height="9"/><path d="M5.5 5V2.5h5V5M1.5 9h13M7 8v2h2V8"/>',
  cup: '<path d="M2.5 5.5h9v4a4 4 0 0 1-4 4h-1a4 4 0 0 1-4-4zM11.5 7H14v2.6h-2.5M5.2 1.5v2M8.8 1.5v2"/>',
  home: '<path d="M1.8 7.6 8 2l6.2 5.6M3.5 6.4v8.1h9V6.4M6.6 14.5v-4h2.8v4"/>',
  folder: '<path d="M1.5 3h5l1.5 2h6.5v8.5h-13z"/>',
  chart: '<path d="M2 13.5h12M2.5 10.5 6 7l3 2.5 4.5-6"/>',
  chat: '<path d="M2 2.75h12v8.5H7.5L4.5 14v-2.75H2z"/>',
  archive: '<rect x="1.5" y="2" width="13" height="4"/><path d="M2.5 6v8h11V6M6 9h4"/>',
  eye: '<path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/>',
  ask: '<rect x="1.5" y="1.5" width="13" height="13"/><path d="M6 6.2a2 2 0 1 1 2.6 1.9c-.4.2-.6.5-.6.9v.8M8 11.6v.2"/>',
  inbox: '<path d="M1.5 9.5 3.5 2.5h9l2 7v4h-13zM1.5 9.5H5l1 2h4l1-2h3.5"/>',
  plug: '<path d="M5.5 1.5v3M10.5 1.5v3M3.5 4.5h9v3a4.5 4.5 0 0 1-9 0zM8 12v2.5"/>',
  mute: '<path d="M2 6h2.5L8 3v10L4.5 10H2zM11 6l4 4M15 6l-4 4"/>',
  speaker: '<path d="M2 6h2.5L8 3v10L4.5 10H2zM11 5.6a3.5 3.5 0 0 1 0 4.8M13 3.6a6.5 6.5 0 0 1 0 8.8"/>',
  wrench: '<path d="M10.5 1.8a3.5 3.5 0 0 0-3.8 4.6L1.8 11.3l2.9 2.9 4.9-4.9a3.5 3.5 0 0 0 4.6-3.8l-2.1 2.1-2-.5-.5-2z"/>',
  body: '<circle cx="8" cy="3" r="1.8"/><path d="M4.8 6.5h6.4L10 10.5H6zM6.5 10.5 6 15M9.5 10.5 10 15"/>',
  desk: '<path d="M1 9h14M2.5 9v5.5M13.5 9v5.5M4.5 9V3h7v6M7 7h2"/>',
  scan: '<path d="M1.5 5V1.5H5M11 1.5h3.5V5M14.5 11v3.5H11M5 14.5H1.5V11"/><circle cx="8" cy="6.6" r="2"/><path d="M4.6 12c.6-1.6 1.9-2.5 3.4-2.5s2.8.9 3.4 2.5"/>',
  shirt: '<path d="M5.5 1.5 1.5 4l1.5 3 1.5-.8v8.3h7V6.2l1.5.8 1.5-3-4-2.5c0 1.2-1 2-2.5 2s-2.5-.8-2.5-2z"/>',
  image: '<rect x="1.5" y="2.5" width="13" height="11"/><path d="m1.5 11 4-4 3 3 2-2 4 4"/><path d="M11 5.4v.2"/>',
  camera: '<path d="M1.5 4.5h3l1.5-2h4l1.5 2h3v9h-13z"/><circle cx="8" cy="8.6" r="2.5"/>',
  lock: '<rect x="2.5" y="7" width="11" height="7.5"/><path d="M5 7V4.5a3 3 0 0 1 6 0V7"/>',
  unlock: '<rect x="2.5" y="7" width="11" height="7.5"/><path d="M5 7V4.5a3 3 0 0 1 5.8-1"/>',
  help: '<circle cx="8" cy="8" r="6.5"/><path d="M6 6.2a2 2 0 1 1 2.6 1.9c-.4.2-.6.5-.6.9v.6M8 11.4v.2"/>',
  undo: '<path d="M5.5 3 2 6.5 5.5 10M2 6.5h8a4 4 0 0 1 0 8H7"/>',
  gamepad: '<path d="M4 4.5h8a3 3 0 0 1 3 3v2a2.5 2.5 0 0 1-4.5 1.5L10 10H6l-.5 1A2.5 2.5 0 0 1 1 9.5v-2a3 3 0 0 1 3-3z"/><path d="M4.5 6.8v2.6M3.2 8.1h2.6M11 7.4v.2M12.5 9v.2"/>',
  cap: '<path d="M8 2.5 1 6l7 3.5L15 6zM4 7.5V11c1 1.3 2.4 2 4 2s3-.7 4-2V7.5M15 6v4"/>',
  briefcase: '<rect x="1.5" y="4.5" width="13" height="9.5"/><path d="M5.5 4.5v-2h5v2M1.5 8.5h13"/>',
  book: '<path d="M2 2.5h4.5A1.5 1.5 0 0 1 8 4v10a1.5 1.5 0 0 0-1.5-1.5H2zM14 2.5H9.5A1.5 1.5 0 0 0 8 4v10a1.5 1.5 0 0 1 1.5-1.5H14z"/>',
  building: '<rect x="3" y="1.5" width="10" height="13"/><path d="M5.5 4.5h1M9.5 4.5h1M5.5 7.5h1M9.5 7.5h1M5.5 10.5h1M9.5 10.5h1M7 14.5v-2h2v2"/>',
  repeat: '<path d="M2 7.5V5.5h10.5M10.5 3l2.5 2.5L10.5 8M14 8.5v2H3.5M5.5 13 3 10.5 5.5 8"/>',
  hourglass: '<path d="M3.5 1.5h9M3.5 14.5h9M4.5 1.5v2.5L8 8l3.5-4V1.5M4.5 14.5V12L8 8l3.5 4v2.5"/>',
  trash: '<path d="M1.5 3.5h13M6 3.5v-2h4v2M3 3.5l1 11h8l1-11M6.5 6.5v5M9.5 6.5v5"/>',
  flag: '<path d="M3 15V1.5M3 2h9.5l-2 3.5 2 3.5H3"/>',
  calendar: '<rect x="1.75" y="3" width="12.5" height="11.25"/><path d="M1.75 6.5h12.5M5 1.25v3.5M11 1.25v3.5M4.5 9h2M9.5 9h2M4.5 11.5h2"/>',
  tag: '<path d="M1.5 1.5h6l7 7-6 6-7-7z"/><path d="M5 5v.2"/>',
  download: '<path d="M8 1.5v9M4.5 7 8 10.5 11.5 7M1.5 11.5v3h13v-3"/>',
  phone: '<rect x="4" y="1.5" width="8" height="13"/><path d="M7 12.2h2"/>',
  link: '<path d="M6.5 9.5 9.5 6.5M7 4.5l1.2-1.2a2.8 2.8 0 0 1 4 4L11 8.5M9 11.5l-1.2 1.2a2.8 2.8 0 0 1-4-4L5 7.5"/>',
  paperclip: '<path d="M10.5 4.5 5.2 9.8a1.6 1.6 0 0 0 2.3 2.3l5.6-5.6a3 3 0 0 0-4.3-4.3L3.2 7.8a4.4 4.4 0 0 0 6.2 6.2l4.1-4.1"/>',
  upload: '<path d="M8 10.5v-9M4.5 5 8 1.5 11.5 5M1.5 11.5v3h13v-3"/>',
};
function ico(name, cls = '') {
  return `<svg class="ico ${cls}" viewBox="0 0 16 16" aria-hidden="true">${LINE_ICONS[name] || ''}</svg>`;
}
const EMOJI_ICON = {
  '⚠': 'warn', '⚡': 'bolt', '⏰': 'bell', '🔔': 'bell', '📝': 'note', '⏱': 'timer', '⚙': 'cog', '🔍': 'search',
  '🙋': 'user', '🙂': 'user', '🧠': 'chip', '🤖': 'bot', '🎨': 'palette', '🧰': 'tools', '☕': 'cup', '🏠': 'home',
  '📁': 'folder', '📈': 'chart', '💬': 'chat', '🗄': 'archive', '👀': 'eye', '✋': 'ask', '📥': 'inbox', '🔌': 'plug',
  '🔇': 'mute', '🔊': 'speaker', '🔧': 'wrench', '🧍': 'body', '🪴': 'desk', '🪞': 'scan', '👗': 'shirt', '🖼': 'image',
  '🤳': 'camera', '🔒': 'lock', '🔓': 'unlock', '↩': 'undo', '❔': 'help', '🎮': 'gamepad', '🎓': 'cap', '💼': 'briefcase',
  '📚': 'book', '🏢': 'building', '📱': 'phone', '🔁': 'repeat', '⏳': 'hourglass', '⌛': 'hourglass', '🗑': 'trash', '🎐': null, '🌙': null, '🪵': null, '🎉': null,
};
const EMOJI_SRC = `(${Object.keys(EMOJI_ICON).join('|')})\\uFE0F?( ?)`;
const EMOJI_FIND = new RegExp(EMOJI_SRC, 'u'), EMOJI_ALL = new RegExp(EMOJI_SRC, 'gu');
const ICON_SKIP = 'script, style, textarea, input, svg, title, code, pre';
function iconize(root) {
  if (!root) return;
  if (root.nodeType === 3) root = root.parentNode;
  if (!root || root.nodeType !== 1 || root.closest?.(ICON_SKIP)) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => EMOJI_FIND.test(n.data) && !n.parentElement.closest(ICON_SKIP) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
  });
  const hits = [];
  while (walker.nextNode()) hits.push(walker.currentNode);
  hits.forEach(n => {
    if (n.parentElement.closest('select, option, optgroup')) { n.data = n.data.replace(EMOJI_ALL, ''); return; }   // no markup in options
    const tpl = document.createElement('template');
    tpl.innerHTML = esc(n.data).replace(EMOJI_ALL, (m, e, space) => EMOJI_ICON[e] ? ico(EMOJI_ICON[e]) + space : '');
    n.replaceWith(tpl.content);
  });
}
new MutationObserver(list => list.forEach(m => {
  if (m.type === 'characterData') iconize(m.target);
  else m.addedNodes.forEach(iconize);
})).observe(document.body, { childList: true, subtree: true, characterData: true });
iconize(document.body);

// ---------------------------------------------------------------- routing
async function route() {
  closeAllModals();
  const m = location.hash.match(/^#\/b\/(\d+)/);
  try {
    syncViewSwitch();
    if (location.hash.startsWith('#/awaken')) await renderAwaken();
    else if (m) await renderBlock(Number(m[1]));
    else if (location.hash.startsWith('#/projects')) await renderHome();
    else if (location.hash.startsWith('#/calendar')) await renderCalendar();
    else if (location.hash.startsWith('#/focus')) await renderFocus();
    else if (location.hash.startsWith('#/review')) await renderReview();
    else if (location.hash.startsWith('#/work')) await renderAgentWork();
    else if (location.hash.startsWith('#/timer')) await renderTimerScreen();
    else await renderHud();
  } catch (e) {
    app.innerHTML = `<div class="text-center py-24"><p class="text-slate-500 mb-4">${esc(e.message)}</p><a href="#/projects" class="text-lavender-700 font-medium">Back to workflow</a></div>`;
  }
}
// The header's HUD / Projects switch: Projects stays lit inside a project too.
function syncViewSwitch() {
  const onHud = location.hash === '' || location.hash === '#/';
  const current = onHud ? 'hud' : location.hash.startsWith('#/focus') ? 'focus' : location.hash.startsWith('#/work') ? 'work' : 'projects';   // a project counts as Projects
  document.body.classList.toggle('one-screen', onHud || /^#\/(projects|calendar|focus|work|b\/)/.test(location.hash));
  document.querySelectorAll('[data-view-switch]').forEach(a => {
    const on = a.dataset.viewSwitch === current;
    a.classList.toggle('bg-lavender-700', on); a.classList.toggle('text-white', on);
    a.classList.toggle('text-slate-600', !on);
    a.setAttribute('aria-current', on ? 'page' : 'false');
  });
}
window.addEventListener('hashchange', route);
const go = id => { location.hash = id == null ? '#/projects' : `#/b/${id}`; };
// Redraw the page and any open block popup after a change, without closing panels.
const refresh = async () => { await renderCurrent(); await refreshPopup(); };

// ---------------------------------------------------------------- home
// A project card lists only its first-level to-dos: phase dot (lock / ✓), name, and for ones with
// to-dos inside, how many are done and a small progress bar. Click one to open it.
function topLevelHTML(nodes) {
  return `<ul class="space-y-0.5">${nodes.map(n => {
    const st = STAGE_STYLE[n.status], kids = n.children || [];
    const mark = n.done ? '<span class="w-3 text-center text-xs text-mint-700">✓</span>'
      : n.locked ? `<span class="w-3 text-butter-700">${ICON.lock}</span>`
      : `<span class="w-3 flex justify-center"><span class="w-2 h-2 rounded-full ${st.dot} ${isLiveStage(n.status) ? 'dot-live' : ''}"></span></span>`;
    return `<li data-go="${n.id}" title="${esc(n.title)} · ${esc(stageLabel(n.status))}${n.locked ? ' · starts when ' + esc(n.blocked_by.join(' and ')) : ''}"
        class="flex items-center gap-2 rounded-lg px-2 py-1 text-sm cursor-pointer hover:bg-lavender-50">
      ${mark}
      <span class="min-w-0 flex-1 truncate ${n.done ? 'text-slate-400 line-through' : 'text-slate-700'}">${esc(n.title)}</span>
      ${kids.length ? `<span class="hud-num shrink-0 text-[11px] text-slate-400">${kids.filter(k => k.done).length}/${kids.length}</span>
        <span class="shrink-0 w-12">${bar(n.progress, 'h-1')}</span>` : ''}
    </li>`;
  }).join('')}</ul>`;
}

// The Workflow title, with the switch between its two views right under it: every project, or the calendar.
function workflowHeadHTML(active, intro) {
  const views = [['projects', '#/projects', 'folder', 'Projects'], ['calendar', '#/calendar', 'calendar', 'Calendar']];
  return `<div class="min-w-0">
    <h1 class="text-2xl font-bold text-slate-900">Workflow</h1>
    <nav class="mt-2 inline-flex items-center rounded-full border border-lavender-200 bg-white/60 p-0.5 text-xs font-semibold font-display uppercase tracking-wide" aria-label="Workflow view">
      ${views.map(([k, href, icon, label]) => `<a href="${href}" data-wf-switch="${k}" class="flex items-center gap-1.5 rounded-full px-3 py-1 ${k === active ? 'bg-lavender-700 text-white' : 'text-slate-600 hover:text-lavender-700'}" ${k === active ? 'aria-current="page"' : ''}>${ico(icon)}${label}</a>`).join('')}
    </nav>
    <p class="text-slate-500 mt-1.5 text-sm">${intro}</p>
  </div>`;
}

async function renderHome() {
  state.data = null;
  const [projects, settings, archived] = await Promise.all([api('GET', '/api/projects'), api('GET', '/api/settings'),
    api('GET', '/api/projects?archived=true').catch(() => [])]);
  projects.forEach(p => registerStages(p.stages));
  document.title = 'Workflow · Nestwork';
  // Every card fits on one screen: a grid sized to the number of projects; each card's tree scrolls inside it.
  const n = projects.length, cols = n <= 3 ? Math.max(n, 1) : n === 4 ? 2 : n <= 6 ? 3 : 4;
  app.innerHTML = `
    <div class="one-screen-fill flex flex-col">
    <div class="shrink-0 flex flex-wrap items-end justify-between gap-4 mb-4">
      ${workflowHeadHTML('projects', 'Break big goals into blocks, nest them as deep as you need, and set what waits for what. Today’s focus is on the <a href="#/" class="text-lavender-700 underline">HUD</a>.')}
      <div class="flex flex-wrap items-center gap-2">
        <button data-act="archived" class="inline-flex items-center gap-1.5 rounded-full border border-slate-300 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50" title="Finished or paused projects: hidden here, still on the calendar">${ico('archive')}Archive${archived.length ? ` (${archived.length})` : ''}</button>
        <a href="#/review" class="inline-flex items-center gap-1.5 rounded-full border border-slate-300 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50" title="Look back at the week and plan the next one">${ico('chart')}Weekly review</a>
        <button data-act="import" class="inline-flex items-center gap-1.5 rounded-full border border-lavender-300 px-3 py-2 text-sm text-lavender-700 hover:bg-lavender-50" title="A Markdown outline or a Nestwork export">${ico('upload')}Import</button>
        <button data-act="new-project" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2 shadow-sm">+ New project</button>
      </div>
    </div>
    ${settings.awakened ? '' : `
      <a href="#/awaken" class="group shrink-0 mb-4 flex items-center gap-4 rounded-3xl border border-lilac-200 bg-gradient-to-r from-blush-50 via-lilac-50 to-baby-50 px-5 py-3 shadow-sm hover:shadow-md transition">
        <span class="w-10 h-10 shrink-0 rounded-full bg-gradient-to-br from-lilac-300 via-baby-300 to-lavender-300 text-white flex items-center justify-center text-lg">✦</span>
        <span class="flex-1"><span class="block font-extrabold text-slate-800">Awaken your agent</span>
        <span class="block text-sm text-slate-500">Give it a name, a personality, a look and a workspace. Then it says hello in its own words.</span></span>
        <span class="shrink-0 rounded-full bg-lavender-700 text-white text-sm font-semibold px-4 py-2 group-hover:bg-lavender-800">Begin ✦</span>
      </a>`}
    ${n ? `
      <div class="proj-grid grid gap-4 sm:grid-cols-2 lg:flex-1 lg:min-h-0" style="--cols:${cols};--rows:${Math.ceil(n / cols)}">
        ${projects.map(p => { const a = ACCENTS[p.id % ACCENTS.length]; return `
          <a href="#/b/${p.id}" class="proj group flex flex-col min-h-0 rounded-3xl bg-white border border-${a}-200 overflow-hidden shadow-sm hover:shadow-md transition">
           <div class="h-1 shrink-0 bg-${a}-300 text-${a}-300"></div>
           <div class="p-4 flex flex-col min-h-0 flex-1">
            <div class="shrink-0 flex items-start justify-between gap-3">
              <h2 class="font-extrabold text-lg text-slate-800 group-hover:text-${a}-700 truncate">${esc(p.title)}</h2>
              <span class="shrink-0 flex items-center gap-1.5">
                <button type="button" data-archive-project="${p.id}" class="proj-archive ${pct(p.progress) === 100 ? 'is-done' : ''} w-7 h-7 rounded-full flex items-center justify-center text-slate-400 hover:text-slate-700 hover:bg-slate-100" title="${pct(p.progress) === 100 ? 'All done: archive it' : 'Archive'} (hidden here, stays on the calendar)" aria-label="Archive ${esc(p.title)}">${ico('archive')}</button>
                <span class="text-sm font-semibold ${pct(p.progress) === 100 ? 'text-mint-700' : 'text-slate-500'}">${pct(p.progress)}%</span>
              </span>
            </div>
            <div class="shrink-0 mt-2">${bar(p.progress, 'h-2', `p${p.id}`)}</div>
            <div class="shrink-0 mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-500">
              <span>${p.leaves_done}/${p.leaves_total} tasks done</span>
              <span class="text-baby-700">${p.ready_now} ready to work on</span>
              ${forecastHTML(p.forecast, true)}
            </div>
            ${p.tree.children.length ? `<div class="mt-3 flex-1 min-h-0 overflow-auto scroll-thin">${topLevelHTML(p.tree.children)}</div>` : ''}
           </div>
          </a>`; }).join('')}
      </div>` : emptyHome()}
    </div>
  `;
  bindBubbles(app);
  const entering = enterPage('home', 0);
  if (entering) { stagger(app.querySelectorAll('.proj'), 'pop-in', 60); animate(app.querySelector('[data-wf-switch="projects"]'), 'tab-on'); }
  animateBars(app, entering);
  app.querySelector('[data-act=new-project]').onclick = newProjectModal;
  app.querySelector('[data-act=import]').onclick = () => importModal();
  app.querySelector('[data-act=archived]').addEventListener('click', () => archivedModal(archived));
  app.querySelectorAll('[data-archive-project]').forEach(b => b.addEventListener('click', e => {
    e.preventDefault(); e.stopPropagation();          // the card is a link
    const p = projects.find(x => x.id === Number(b.dataset.archiveProject));
    setArchived(p, true);
  }));
  app.querySelector('[data-act=new-project-2]')?.addEventListener('click', newProjectModal);
}

// Labels for /api/today groups (overdue, due soon, started, ready…), used by the HUD.
const TODAY_STYLE = {
  overdue: 'bg-blush-100 text-blush-700', due_soon: 'bg-butter-100 text-butter-700',
  in_progress: 'bg-peach-100 text-peach-700', ready: 'bg-baby-100 text-baby-700',
  in_review: 'bg-lilac-100 text-lilac-700', blocked: 'bg-slate-100 text-slate-500',
};
const TODAY_SHORT = { overdue: 'Overdue', due_soon: 'Due soon', in_progress: 'Started', ready: 'Ready', in_review: 'In review', blocked: 'Blocked' };

function emptyHome() {
  return `
    <div class="rounded-2xl border-2 border-dashed border-slate-300 bg-white p-10 text-center">
      <div class="mx-auto w-14 h-14 rounded-2xl bg-lavender-100 flex items-center justify-center mb-4">
        <div class="w-8 h-8 rounded-lg bg-lavender-200 flex items-center justify-center"><div class="w-3 h-3 rounded bg-white"></div></div>
      </div>
      <h2 class="text-lg font-semibold">No projects yet</h2>
      <p class="text-slate-500 mt-1 max-w-md mx-auto">Start from a template (a game, a university application, a job hunt…) or a blank project. You can change everything afterwards.</p>
      <button data-act="new-project-2" class="mt-5 rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">Create your first project</button>
    </div>`;
}

async function newProjectModal() {
  state.templates ??= await api('GET', '/api/templates');
  let chosen = 'blank';
  openModal(`
    <h2 class="text-lg font-semibold mb-4">New project</h2>
    <label class="block text-sm font-medium text-slate-700 mb-1" for="np-title">Name</label>
    <input id="np-title" class="w-full rounded-lg border border-slate-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300" placeholder="e.g. My RPG, Masters 2027, Find a new job">
    <p class="text-sm font-medium text-slate-700 mt-5 mb-2">Start from</p>
    <div class="grid sm:grid-cols-2 gap-2" id="np-templates">
      ${state.templates.map(t => `
        <button type="button" data-key="${t.key}" class="tpl text-left rounded-lg border p-3 hover:border-lavender-300">
          <div class="font-medium"><span class="mr-1.5">${t.icon}</span>${esc(t.name)}</div>
          <div class="text-xs text-slate-500 mt-1">${esc(t.description)}</div>
        </button>`).join('')}
    </div>
    <div id="np-phases" class="mt-5">
      <p class="text-sm font-medium text-slate-700">Your phases</p>
      <p class="text-xs text-slate-500 mb-2">The columns your to-dos move through, from first to last. The last one means done. You can change them any time.</p>
      <div id="np-phase-list" class="space-y-1.5"></div>
      <button type="button" id="np-phase-add" class="mt-2 rounded-full border border-dashed border-lavender-300 px-3 py-1 text-sm text-lavender-700 hover:bg-lavender-50">+ Add phase</button>
    </div>
    <div class="flex justify-end gap-2 mt-6">
      <button data-close class="rounded-lg px-4 py-2 text-slate-600 hover:bg-slate-100">Cancel</button>
      <button id="np-create" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">Create</button>
    </div>`, (root) => {
    const title = root.querySelector('#np-title');
    const paint = () => root.querySelectorAll('.tpl').forEach(b => {
      const on = b.dataset.key === chosen;
      b.classList.toggle('border-lavender-300', on); b.classList.toggle('ring-2', on);
      b.classList.toggle('ring-lavender-100', on); b.classList.toggle('border-slate-200', !on);
    });
    // Blank projects start with empty phase fields to fill in.
    const phaseBox = root.querySelector('#np-phases'), phaseList = root.querySelector('#np-phase-list');
    const examples = ['To do', 'Doing', 'Done'];
    const phaseRow = (i, n) => `<div class="np-phase flex items-center gap-2">
      <span class="w-2.5 h-2.5 shrink-0 rounded-full bg-${i === 0 ? 'lavender' : i === n - 1 ? 'mint' : ['baby', 'peach', 'lilac', 'butter', 'blush'][(i - 1) % 5]}-300"></span>
      <input maxlength="40" placeholder="e.g. ${examples[Math.min(i, 2)]}" class="flex-1 rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-lavender-300">
      <button type="button" data-del class="p-1 rounded-lg text-slate-400 hover:text-blush-700 hover:bg-blush-50" aria-label="Remove phase">✕</button></div>`;
    const phaseNames = () => [...phaseList.querySelectorAll('input')].map(i => i.value.trim());
    const drawPhases = names => {
      phaseList.innerHTML = names.map((_, i) => phaseRow(i, names.length)).join('');
      phaseList.querySelectorAll('input').forEach((inp, i) => { inp.value = names[i]; });
    };
    drawPhases(['', '', '']);
    root.querySelector('#np-phase-add').onclick = () => {
      const names = phaseNames(); names.push(''); drawPhases(names);
      animate(phaseList.lastElementChild, 'pop-in');
      phaseList.lastElementChild.querySelector('input').focus();
    };
    phaseList.addEventListener('click', e => {
      if (!e.target.closest('[data-del]')) return;
      const names = phaseNames();
      if (names.length <= 2) return;
      names.splice([...phaseList.children].indexOf(e.target.closest('.np-phase')), 1);
      drawPhases(names);
    });
    const showPhases = () => phaseBox.classList.toggle('hidden', chosen !== 'blank');
    showPhases();
    root.querySelectorAll('.tpl').forEach(b => b.onclick = () => {
      chosen = b.dataset.key; paint(); showPhases();
      if (!title.value.trim() && chosen !== 'blank') title.placeholder = state.templates.find(t => t.key === chosen).name;
    });
    paint();
    const create = async () => {
      const name = title.value.trim() || (chosen !== 'blank' ? state.templates.find(t => t.key === chosen).name : '');
      if (!name) { title.focus(); return; }
      const body = { title: name, template: chosen };
      if (chosen === 'blank') {
        body.stages = phaseNames().filter(Boolean);
        if (body.stages.length < 2) {
          toast('Write at least two phases: where work starts and where it’s done.', 'error');
          phaseList.querySelector('input:placeholder-shown')?.focus();
          return;
        }
      }
      const r = await attempt(() => api('POST', '/api/blocks', body));
      if (r) { closeModal(); go(r.block.id); }
    };
    root.querySelector('#np-create').onclick = create;
    title.onkeydown = e => { if (e.key === 'Enter') create(); };
    title.focus();
  });
}

// ---------------------------------------------------------------- block page
async function renderBlock(id) {
  const data = await api('GET', `/api/blocks/${id}`);
  state.data = data;
  registerStages(data.stages);
  const b = data.block;
  document.title = `${b.title} · Nestwork`;
  const views = [['board', 'Board'], ['tree', 'Tree'], ['outline', 'Outline'], ['next', 'What can I do now?'], ['notes', `📝 Notes${b.notes_count ? ` (${b.notes_count})` : ''}`]];
  // Every block starts as a single task with a description; its to-do list appears once asked for.
  const hasList = data.is_root || data.children.length > 0 || state.breakdown.has(id);
  // Redrawing the same page (after a move, an edit, an agent change) keeps where you were scrolled to.
  const oldView = app.querySelector('#view');
  const keep = motion.page === id ? { view: motion.view, top: oldView?.scrollTop || 0, left: oldView?.scrollLeft || 0, win: scrollY } : null;
  // The header can fold down to its title and progress bar (remembered across pages).
  const headFolded = localGet('nestwork.headFolded') === '1';
  const fold = headFolded ? ' hidden' : '';

  app.innerHTML = `
    <div class="one-screen-fill flex flex-col">
    <nav class="shrink-0 sticky top-14 z-10 -mx-4 px-4 py-1.5 bg-white/85 backdrop-blur lg:static lg:mx-0 lg:px-0 lg:bg-transparent lg:backdrop-blur-none flex flex-wrap items-center gap-1 text-sm text-slate-500 mb-2">
      <a href="${data.breadcrumb.length ? `#/b/${data.breadcrumb[data.breadcrumb.length - 1].id}` : '#/projects'}" class="mr-2 inline-flex items-center gap-1 rounded-full border border-lavender-200 px-2.5 py-0.5 text-xs font-semibold text-lavender-700 hover:bg-lavender-50" title="Up one level">← Back</a>
      <a href="#/projects" class="hover:text-lavender-700">Workflow</a>
      ${data.breadcrumb.map(c => `<span class="text-slate-300">/</span><a href="#/b/${c.id}" class="hover:text-lavender-700">${esc(c.title)}</a>`).join('')}
      <span class="text-slate-300">/</span><span class="text-slate-800 font-medium">${esc(b.title)}</span>
    </nav>

    <section class="shrink-0 rounded-3xl bg-white/90 border border-lavender-100 p-4 shadow-sm mb-3 lg:max-h-[38vh] lg:overflow-y-auto scroll-thin">
      <div class="block-head flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <button data-act="fold-head" class="w-6 h-6 -ml-1 shrink-0 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-expanded="${!headFolded}" title="${headFolded ? 'Expand' : 'Collapse'} the header" aria-label="${headFolded ? 'Expand' : 'Collapse'} the header"><span class="transition ${headFolded ? '' : 'rotate-90'}">${ICON.caret}</span></button>
            <h1 class="text-2xl font-extrabold text-slate-800 break-words">${esc(b.title)}</h1>
            ${data.is_root ? '' : `<span class="rounded-full px-2 py-0.5 text-xs font-medium ${STAGE_STYLE[b.status].pill}">${esc(stageLabel(b.status))}</span>`}
            ${dueBadge(b.due_date, b.done, b.due_time)}${priorityBadge(b)}${repeatBadge(b)}${estimateBadge(b)}${tagChips(b)}
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button data-act="new-todo" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-sm px-4 py-1.5" title="Add a to-do here or inside any to-do below (N)">+ New to-do</button>
          <button data-act="edit-self" class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">Edit</button>
          <button data-act="stages" class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50" title="Rename, add, remove or reorder this project’s phases">Phases</button>
        </div>
      </div>
      <div class="head-more mt-2${fold}">
        <label for="desc" class="sr-only">Description</label>
        <textarea id="desc" rows="1" class="block w-full resize-none overflow-hidden rounded-2xl border border-transparent bg-lavender-50/50 hover:bg-lavender-50 focus:bg-white focus:border-lavender-200 px-3 py-2 text-slate-700 placeholder-slate-400 focus:outline-none transition" placeholder="What is this about? Add details, links, acceptance criteria…">${esc(b.notes)}</textarea>
        <p id="desc-status" class="h-3.5 text-[11px] text-slate-400"></p>
        ${attachmentsPanelHTML(b)}
      </div>
      <div class="head-more${fold}">${data.is_root ? '' : stagePicker(b)}</div>
      ${b.children_total ? `
      <div class="mt-1 flex items-center gap-3">
        <div class="flex-1">${bar(b.progress, 'h-2.5', `b${b.id}`)}</div>
        <span class="text-sm font-semibold w-12 text-right">${pct(b.progress)}%</span>
      </div>
      <p class="head-more mt-1 text-xs text-slate-500${fold}">
        ${b.children_done} of ${b.children_total} to-dos here done${b.leaves_total !== b.children_total ? ` · ${b.leaves_done} of ${b.leaves_total} tasks across every level` : ''}
      </p>` : ''}
      ${data.is_root || b.children_total ? '' : b.track ? trackHTML(b, true) : trackSetupHTML(b)}
      ${data.is_root && b.archived_at ? `<div class="head-more mt-2${fold} flex flex-wrap items-center gap-2 rounded-xl bg-slate-100 border border-slate-200 px-3 py-2 text-sm text-slate-600">${ico('archive')}<span class="flex-1">Archived on ${esc(fmtDay(b.archived_at.slice(0, 10)))}: hidden from Workflow, the HUD, Today and reminders.</span><button data-act="unarchive" class="rounded-full border border-slate-300 px-3 py-1 text-xs font-semibold hover:bg-white">Unarchive</button></div>` : ''}
      ${data.forecast?.open ? `<div class="head-more mt-2 text-sm${fold}">${forecastHTML(data.forecast)}</div>` : ''}
      ${data.is_root ? '' : `<div class="head-more mt-3 space-y-2 text-sm${fold}">
        ${b.warning ? `<div class="rounded-xl bg-blush-50 border border-blush-200 px-3 py-2 text-blush-700">⚠️ “${esc(b.title)}” is further along than its rules allow. Still waiting until ${esc(b.warning_reasons.join(' and '))}.</div>` : ''}
        ${b.locked && !b.warning ? `<div class="flex items-start gap-2 rounded-xl bg-butter-50 border border-butter-200 px-3 py-2 text-butter-700">${ICON.lock}<span>Can't start until ${b.blocked_by.map(u => `<a class="underline" href="#/b/${u.id}">${esc(u.text)}</a>`).join(' and ')}. Everything in here stays in “${esc(stageLabel(firstKey()))}” until then.</span></div>` : ''}
        ${!b.locked && b.gate && !b.done ? `<div class="rounded-xl bg-lavender-50 border border-lavender-100 px-3 py-2 text-lavender-700">→ Can move to “${esc(stageLabel(b.gate.stage))}” when ${esc(b.gate.reasons.join(' and '))}.</div>` : ''}
        ${b.automation.length ? `<div class="rounded-xl bg-mint-50 border border-mint-100 px-3 py-2 text-mint-700">
          <p class="font-bold mb-0.5">⚡ Automatic</p>
          <ul class="list-disc pl-5 space-y-0.5">${b.automation.map(a => `<li>${esc(a)}</li>`).join('')}</ul>
          <button data-act="edit-rules" class="mt-1 text-xs underline">Edit rules</button>
        </div>` : `<button data-act="edit-rules" class="text-xs text-lavender-700 underline">⚡ Add rules: decide when this moves automatically</button>`}
      </div>`}
    </section>

    ${hasList ? `
    ${data.children.length ? filterBarHTML(data) : ''}
    <div class="shrink-0 flex items-center gap-1 mb-3 border-b border-slate-200 overflow-x-auto overflow-y-hidden">
      ${views.map(([k, l]) => `<button data-view="${k}" class="px-3 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap ${state.view === k ? 'border-lavender-700 text-lavender-800' : 'border-transparent text-slate-500 hover:text-slate-800'}">${l}</button>`).join('')}
    </div>
    <div id="view" class="lg:flex-1 lg:min-h-0 lg:overflow-auto scroll-thin"></div>` : `
    <div class="shrink-0 rounded-3xl border-2 border-dashed border-lavender-200 bg-white/60 p-8 text-center">
      <p class="font-bold text-slate-600">“${esc(b.title)}” is a single task.</p>
      <p class="text-sm text-slate-400 mt-1">Need more steps? Give it its own to-do list. Each item is a block you can describe and break down too.</p>
      <button data-act="breakdown" class="mt-4 rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2 shadow-sm">+ Add a to-do list inside</button>
    </div>
    <div id="view-notes" class="mt-3 rounded-3xl border border-lavender-100 bg-white/70 p-4 shadow-sm lg:flex-1 lg:min-h-0">${notesPanelHTML(b, { fill: true })}</div>`}
    </div>
  `;

  bindBubbles(app.querySelector('section'));
  bindTrack(app.querySelector('section'));
  // Folding tears the extra parts away, then the panel shrinks; unfolding grows it and wipes them back in.
  app.querySelector('[data-act=fold-head]').onclick = async e => {
    const btn = e.currentTarget, sec = btn.closest('section'), folded = btn.getAttribute('aria-expanded') === 'true';
    if (sec.classList.contains('head-folding')) return;
    const parts = [...app.querySelectorAll('.head-more')];
    localSet('nestwork.headFolded', folded ? '1' : '0');
    btn.setAttribute('aria-expanded', String(!folded));
    btn.title = btn.ariaLabel = `${folded ? 'Expand' : 'Collapse'} the header`;
    btn.firstElementChild.classList.toggle('rotate-90', !folded);
    const from = sec.offsetHeight;
    if (folded) await animateOut(parts, 'head-out', 180);
    parts.forEach(el => el.classList.toggle('hidden', folded));
    const desc = app.querySelector('#desc');   // it was sized while hidden
    if (!folded && desc) { desc.style.height = 'auto'; desc.style.height = desc.scrollHeight + 'px'; }
    const to = sec.offsetHeight;
    sec.style.height = `${from}px`;
    sec.classList.add('head-folding', ...(folded ? ['fold-up'] : []));
    void sec.offsetHeight;
    sec.style.height = `${to}px`;
    if (!folded) stagger(parts, 'head-in', 45);
    setTimeout(() => { sec.classList.remove('head-folding', 'fold-up'); sec.style.height = ''; }, 420);
  };
  app.querySelector('[data-act=edit-self]').onclick = () => editDrawer(b.id, data.is_root);
  app.querySelectorAll('[data-act=edit-rules]').forEach(el => el.onclick = () => editDrawer(b.id));
  app.querySelector('[data-act=stages]').onclick = stagesModal;
  app.querySelector('[data-act=unarchive]')?.addEventListener('click', () => setArchived(b, false));
  app.querySelector('[data-act=new-todo]').onclick = () => newTodoModal();
  app.querySelectorAll('[data-view]').forEach(btn => btn.onclick = async () => {
    if (btn.dataset.view === state.view) return;
    state.view = btn.dataset.view; localSet('nestwork.view', state.view);
    const old = app.querySelector('#view');
    if (old) await animateOut([old], 'view-out', 150);
    route();
  });
  setupDescription(b);
  bindAttachments(app, b);
  app.querySelectorAll('[data-stage]').forEach(btn => btn.onclick = async () => {
    if (btn.dataset.stage === b.status) return;
    if (await attempt(() => api('POST', `/api/blocks/${b.id}/move`, { status: btn.dataset.stage }))) route();
  });
  app.querySelector('[data-act=breakdown]')?.addEventListener('click', async () => {
    state.breakdown.add(id); state.view = 'board'; localSet('nestwork.view', 'board');
    await route();
    editDrawer(null, false, { parent: b, status: childPhase(b.status) });
  });
  const entering = enterPage(id, data.breadcrumb.length + 1);
  const viewKey = hasList ? state.view : 'single';
  const viewChanged = !entering && motion.view !== viewKey;
  motion.view = viewKey;
  motion.entering = entering || viewChanged;
  animateBubbles(app.querySelector('section'), entering);
  animateBars(app.querySelector('section'), entering);
  const view = app.querySelector('#view');
  if (viewChanged) {
    animate(view || app.querySelector('[data-act=breakdown]')?.parentElement, 'view-in');
    animate(app.querySelector(`[data-view="${state.view}"]`), 'tab-on');
  }
  if (view) {
    if (state.view === 'tree') renderTree(view, data);
    else if (state.view === 'outline') renderOutline(view, data);
    else if (state.view === 'next') renderNext(view, data);
    else if (state.view === 'notes') renderNotesView(view, data);
    else renderBoard(view, data);
    if (keep && keep.view === viewKey) { view.scrollTop = keep.top; view.scrollLeft = keep.left; }
    bindFilterBar(app, data);
    applyFilter(app, data);
    applyChain(app, data);
  } else bindNotes(app, b);
  if (keep) window.scrollTo(0, keep.win);
  motion.entering = false;
  askApprovals(data);
}

// ---------------------------------------------------------------- installed app
// On a phone (through Tailscale) or when opened from the Home Screen: the service worker lets the app open
// without a connection to the Mac and launch fast (nestwork/static/sw.js). Not used on localhost.
const isInstalledApp = matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
if ('serviceWorker' in navigator && (location.hostname.endsWith('.ts.net') || isInstalledApp)) {
  navigator.serviceWorker.register('/sw.js').catch(() => { /* no offline launch: everything else works */ });
}

// ---------------------------------------------------------------- the agent's avatar (used by chat, HUD and gallery)
// Here rather than in gallery.js so it exists before the chat restores its history on page load.
const gal = { items: [], avatar: '' };

const avatarURL = () => `/api/agent/avatar?v=${encodeURIComponent(gal.avatar || 'face')}`;
// The agent's picture as a button that opens the profile picture; falls back to ✦ when there's none.
function agentAvatarHTML(size = 'w-8 h-8', text = 'text-sm') {
  return `<button type="button" data-agent-avatar class="relative ${size} shrink-0 rounded-full overflow-hidden ring-2 ring-lilac-200 hover:ring-lavender-300 transition" title="Profile picture" aria-label="Show the profile picture">
    <img src="${avatarURL()}" alt="" class="w-full h-full object-cover" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'w-full h-full bg-gradient-to-br from-lilac-300 via-baby-300 to-lavender-300 text-white flex items-center justify-center ${text}',textContent:'✦'}))">
  </button>`;
}
// After the profile picture changes: every avatar on screen reloads.
function refreshAvatars() {
  document.querySelectorAll('[data-agent-avatar]').forEach(b => { b.outerHTML = agentAvatarHTML(...avatarSize(b)); });
}
function avatarSize(b) {
  const size = [...b.classList].filter(c => /^w-|^h-/.test(c)).join(' ') || 'w-8 h-8';
  return [size, size.includes('w-6') ? 'text-[11px]' : 'text-sm'];
}

// ---------------------------------------------------------------- undo (⌘Z / Ctrl+Z)
// Every change to projects and to-dos, yours or your agent's, is one step back (see nestwork/undo.py).
const UNDO_KEY = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘Z' : 'Ctrl+Z';
async function undoLast() {
  const r = await attempt(() => api('POST', '/api/undo'));
  if (r) { if (conn.v) conn.v.undo = r.next; await refresh(); }
}
document.addEventListener('keydown', e => {
  if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || e.key.toLowerCase() !== 'z') return;
  const el = document.activeElement;
  if (el && (el.matches('input, textarea, select') || el.isContentEditable)) return;   // the field's own undo
  if (location.hash.startsWith('#/awaken')) return;
  e.preventDefault();
  undoLast();
});

// ---------------------------------------------------------------- auto-refresh
// Pages update by themselves when something changes elsewhere (another tab, or an AI agent).
let knownVersion = null, knownFiles = null;
// The last check against the server, shown on the HUD's status strip: did it answer, when, and what it said.
const conn = { ok: null, at: null, v: null };
function isBusy() {
  const el = document.activeElement;
  return el && !el.closest('#chat') && (el.matches('input, textarea, select') || el.isContentEditable);
}
async function poll(force = false) {
  if (document.hidden && !force) return;
  let v;
  try { v = await api('GET', '/api/version'); } catch { conn.ok = false; return; }
  Object.assign(conn, { ok: true, at: Date.now(), v });
  const badge = document.getElementById('pending-badge');
  badge.textContent = v.pending;
  badge.classList.toggle('hidden', !v.pending);
  window.onChatVersion?.(v);
  document.getElementById('work-led')?.classList.toggle('hidden', !v.agent_work_running);
  window.onTimerRev?.(v.timer_rev);
  if (knownFiles !== null && v.files !== knownFiles) { settingsUI.onFilesChanged?.(); window.refreshChatProfile?.(); }
  knownFiles = v.files;
  if (knownVersion === null) { knownVersion = v.version; return; }
  if (v.version === knownVersion) return;
  const settingsOpen = modalRoot.querySelector('#st-proposals');
  if (settingsOpen) drawProposals(settingsOpen.closest('[role=dialog]'), await api('GET', '/api/proposals'));
  // Don't redraw under an open panel or while typing; the next check catches up. `force` is used
  // right after approving proposals in the Settings panel, where redrawing the page behind it is fine.
  if (!force && (modalRoot.querySelector('[data-backdrop]:not(.closing)') || isBusy())) return;
  knownVersion = v.version;
  await renderCurrent();
}
// Redraw the current page without closing an open panel (unlike route()).
async function renderCurrent() {
  if (location.hash.startsWith('#/awaken')) return;   // don't reset the awakening steps
  const m = location.hash.match(/^#\/b\/(\d+)/);
  try { m ? await renderBlock(Number(m[1])) : location.hash.startsWith('#/projects') ? await renderHome() : location.hash.startsWith('#/calendar') ? await renderCalendar() : location.hash.startsWith('#/focus') ? await renderFocus() : location.hash.startsWith('#/review') ? await renderReview() : location.hash.startsWith('#/work') ? await renderAgentWork() : location.hash.startsWith('#/timer') ? await renderTimerScreen() : await renderHud(); } catch { /* the page shows its own error on the next navigation */ }
}
setInterval(poll, 3000);
poll(true);
document.addEventListener('visibilitychange', () => poll());
document.getElementById('settings-btn').onclick = () => settingsModal();

// Theme: follow the system (Auto) or force light/dark (Settings → Appearance). Colors are CSS variables in index.html.
function applyTheme(t) {
  if (t === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
}
let theme = localGet('nestwork.theme') || 'auto';
applyTheme(theme);
function setTheme(t) {
  theme = t;
  localSet('nestwork.theme', t);
  document.documentElement.classList.add('theme-switching');
  applyTheme(t);
  setTimeout(() => document.documentElement.classList.remove('theme-switching'), 450);
}

// Start once every script (awaken.js, chat.js) has loaded.
window.addEventListener('DOMContentLoaded', route);
