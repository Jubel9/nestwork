// The HUD (#/): the first page. Clock, agent + chat toggle, today's tasks, what's coming up, a focus
// timer, the week ahead and every project at a glance. The projects list lives at #/projects.
// Uses helpers from app.js (api, esc, attempt, toast, bar, …) and chat.js (setChatOpen, sendMessage).

const hud = {
  projects: [],
  timer: null,      // the focus timer, loaded below
  ticking: null,
};


// ---------------------------------------------------------------- small helpers
const pad = n => String(n).padStart(2, '0');
const isoDay = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
function weekNumber(d) {   // ISO week
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
  return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5 + 1) / 7);
}
function greeting(h) { return h < 5 ? 'Still up' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'; }
function relTime(at) {
  const mins = Math.round((at - Date.now()) / 60e3);
  if (mins < 1) return 'now';
  if (mins < 60) return `in ${mins} min`;
  const h = Math.round(mins / 60);
  if (h < 24) return `in ${h} h`;
  const d = Math.round(h / 24);
  return d === 1 ? 'tomorrow' : `in ${d} days`;
}
const CLOCK_FMT = new Intl.DateTimeFormat([], { hour: '2-digit', minute: '2-digit' });   // follows the 12/24 h of your locale
const CHAT_ICON = '<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M2 2.75h12v8.5H7.5L4.5 14v-2.75H2z"/></svg>';
function agoText(at) {
  const s = Math.max(0, Math.round((Date.now() - at) / 1000));
  return s < 5 ? 'just now' : s < 60 ? `${s} s ago` : s < 3600 ? `${Math.floor(s / 60)} min ago` : `${Math.floor(s / 3600)} h ago`;
}
const fmtClock = ms => { const s = Math.max(0, Math.ceil(ms / 1000)); return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`; };
// The stopwatch counts up: 12:34, then 1:02:03 after an hour.
const fmtElapsed = ms => { const s = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(s / 3600); return h ? `${h}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}` : `${pad(Math.floor(s / 60))}:${pad(s % 60)}`; };

// A HUD panel: corner brackets, a small label, and content. `grow` panels take the
// column's leftover height on large screens; their .hud-scroll part scrolls inside the panel.
// `count` is a real number shown after the label; `led` is a status light, only where it reports
// something true right now ({ cls, title, attr }).
function panel(label, body, { cls = '', right = '', grow = false, count = null, led = null, attrs = '' } = {}) {
  const light = led ? `<span class="hud-led ${led.cls}" title="${esc(led.title)}" ${led.attr || ''}></span>` : '';
  return `<section class="hud-panel hud-in flex flex-col ${grow ? 'hud-grow' : 'shrink-0'} ${cls}" ${attrs}>
    <div class="shrink-0 flex items-center justify-between gap-2 mb-2.5">
      <p class="hud-label">${light}<span>${label}</span>${count != null ? `<span class="hud-count">· ${count}</span>` : ''}</p>${right}
    </div>
    ${body}
  </section>`;
}

// ---------------------------------------------------------------- page
async function renderHud() {
  state.data = null;
  const [projects, today, profile, version, rem, history, settings] = await Promise.all([
    api('GET', '/api/projects'), api('GET', '/api/today'), api('GET', '/api/agent/profile'),
    api('GET', '/api/version'), api('GET', '/api/reminders?days=7'),
    api('GET', '/api/chat/history').catch(() => []),
    api('GET', '/api/settings'),
  ]);
  hud.plan = await api('GET', '/api/plan').then(r => r.plan).catch(() => null);
  api('GET', '/api/focus/labels').then(r => { hud.labels = r.labels; }).catch(() => {});
  projects.forEach(p => registerStages(p.stages));
  hud.projects = projects;
  hud.lastToday = today;
  gal.avatar = profile.avatar || '';
  Object.assign(conn, { ok: true, at: Date.now(), v: version });   // this render just heard from the server
  document.title = 'Nestwork · HUD';

  app.innerHTML = `
    <div class="hud one-screen-fill flex flex-col">
      ${statusStrip(version, profile, settings)}
      <div class="grid grid-cols-1 gap-3 lg:grid-cols-12 lg:flex-1 lg:min-h-0">
        <div class="hud-col lg:col-span-4">${clockPanel(profile)}${tasksPanel(today, projects)}${projectsPanel(projects)}</div>
        <div class="hud-col lg:col-span-5 order-first lg:order-none">${timerPanel()}${upNextPanel(rem.upcoming, today)}${weekPanel(today)}</div>
        <div class="hud-col lg:col-span-3">${vitalsPanel(projects, today, version)}${agentPanel(profile, version, history, settings)}${usagePanel()}</div>
      </div>
    </div>`;

  const entering = enterPage('hud', 0);
  if (entering) stagger(app.querySelectorAll('.hud-in'), 'pop-in', 75, 700);
  animateBars(app, entering);
  bindHud();
  tickHud();
  loadUsage();
}

// ---------------------------------------------------------------- panels
function statusStrip(v, profile, settings) {
  const items = [
    `<span class="flex items-center gap-1.5" title="Whether this page can reach the Nestwork server"><span data-hud-conn-dot class="hud-dot live text-mint-300 bg-mint-300"></span><span data-hud-conn>Connected · ${esc(location.host)}</span></span>`,
    `<span title="This page checks the server every few seconds and redraws when something changed">Checked <span data-hud-checked>just now</span></span>`,
    profile.claude_found ? `<span>Agent <span data-hud-agent>${v.chat_running ? '<span class="text-peach-700">working…</span>' : 'idle'}</span></span>` : '<span class="text-blush-700">⚠ Claude Code not found</span>',
    `<span title="What your agent may change">Access: ${esc({ read_only: 'read only', ask: 'ask first', full: 'full' }[settings.agent_access] || settings.agent_access || '?')}</span>`,
  ];
  return `<div data-hud-strip class="hud-in shrink-0 mb-3 flex flex-wrap items-center gap-x-5 gap-y-1 rounded-full border border-lavender-100 bg-white/60 backdrop-blur px-4 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-slate-500">
    <span class="text-lavender-700 neon-text">HUD</span>${items.join('')}
    <span class="ml-auto hidden xl:inline normal-case tracking-normal font-sans font-normal text-slate-400"><kbd class="hud-kbd">V</kbd> switch view · <kbd class="hud-kbd">C</kbd> chat · <kbd class="hud-kbd">F</kbd> timer · <kbd class="hud-kbd">${SPOT_KEY}</kbd> search</span>
    <a href="#/projects" class="ml-auto xl:ml-0 normal-case tracking-normal font-sans text-lavender-700 hover:underline">Workflow →</a>
  </div>`;
}

function clockPanel(profile) {
  const name = profile.user_name ? `, ${esc(profile.user_name)}` : '';
  return panel('Clock', `
    <div class="flex items-baseline gap-2">
      <span data-hud-time class="hud-num text-4xl font-extrabold text-slate-900 leading-none">--:--</span>
      <span data-hud-sec class="hud-num text-lg font-bold text-lilac-300">:--</span>
      <span data-hud-ampm class="text-xs font-extrabold uppercase text-slate-400"></span>
    </div>
    <p class="mt-1 text-sm text-slate-500"><span data-hud-greet>${greeting(new Date().getHours())}</span>${name} · <span data-hud-date class="font-semibold text-slate-700"></span></p>
    <div class="mt-2">
      <div class="flex justify-between text-[11px] font-semibold text-slate-400 mb-1"><span>Day</span><span data-hud-dayleft></span></div>
      <div class="h-1.5 rounded-full bg-slate-200/60 overflow-hidden"><div data-hud-dayfill class="h-1.5 rounded-full bg-gradient-to-r from-butter-300 via-peach-300 to-blush-300 shadow-[0_0_calc(var(--glow)*8px)_rgb(var(--c-peach-300)/.7)]" style="width:0"></div></div>
    </div>`, { cls: '', right: '<span data-hud-week class="hud-label"></span>' });
}

function agentPanel(profile, v, history, settings) {
  const name = profile.agent_name || 'Agent';
  const last = [...history].reverse().find(m => m.role === 'assistant' && m.text);
  const open = document.body.classList.contains('chat-open');
  // The last reply gets the panel's spare height and scrolls inside it; the ask box is one slim line.
  return panel('Agent', `
    <div class="shrink-0 flex items-center gap-3">
      <div class="relative w-10 h-10 shrink-0">
        ${agentAvatarHTML('w-10 h-10', 'text-xl')}
        <span class="absolute bottom-0 right-0 w-3.5 h-3.5 rounded-full ring-2 ring-white ${v.chat_running ? 'bg-peach-300 animate-pulse' : profile.claude_found ? 'bg-mint-300' : 'bg-slate-300'}"></span>
      </div>
      <div class="min-w-0 flex-1">
        <button type="button" data-agent-gallery class="block max-w-full font-extrabold text-slate-800 truncate hover:text-lavender-700 hover:underline decoration-dotted underline-offset-4" title="Open ${esc(name)}’s gallery">${esc(name)}</button>
        <p data-hud-agent-status class="text-xs text-slate-400 truncate">${agentStatus(profile.claude_found, v)}</p>
      </div>
      <button data-hud-chat aria-pressed="${open}" class="relative shrink-0 rounded-full border px-3 py-1.5 text-sm font-bold ${open ? 'bg-lavender-700 border-lavender-700 text-white' : 'bg-white border-lavender-200 text-lavender-700 hover:bg-lavender-50'}">
        <span class="inline-flex items-center gap-1.5">${CHAT_ICON}${open ? 'Hide chat' : 'Chat'}</span>${v.chat_unread && !open ? `<span class="absolute -top-1.5 -right-1.5 min-w-[1.25rem] h-5 px-1 rounded-full bg-butter-300 text-[11px] leading-5 text-center text-slate-900">${v.chat_unread}</span>` : ''}
      </button>
    </div>
    <div class="mt-2 flex-1 min-h-[3.5rem] max-h-56 lg:max-h-none overflow-y-auto overscroll-contain scroll-thin rounded-2xl rounded-tl-md bg-lavender-50 border border-lavender-100 px-3 py-2">
      ${last ? `<div class="chat-md text-sm text-slate-700 break-words">${md(last.text)}</div>`
        : `<p class="text-sm text-slate-400">No messages yet. Ask ${esc(name)} anything below.</p>`}
    </div>
    ${settings.awakened ? '' : '<a href="#/awaken" class="shrink-0 mt-1.5 block text-[11px] font-semibold text-lilac-700 hover:underline">✦ Awaken your agent: give it a name and a look →</a>'}
    <form data-hud-ask class="shrink-0 mt-1.5 flex items-center gap-1.5 rounded-full border border-lavender-200 bg-white pl-2.5 pr-1 py-0.5 focus-within:ring-2 focus-within:ring-lavender-300">
      <input name="q" autocomplete="off" placeholder="Ask ${esc(name)}…" class="flex-1 min-w-0 bg-transparent text-xs py-1 focus:outline-none">
      <button class="shrink-0 w-6 h-6 rounded-full bg-lavender-700 text-white text-xs font-bold hover:bg-lavender-800" aria-label="Send">↑</button>
    </form>`, { cls: '', grow: true, led: agentLed(profile, v.chat_running) });
}

// A to-do's daily pomodoro plan: one mark per planned session, lit for each one finished today.
function pomoPips(item) {
  const n = item?.pomodoros || 0, done = item?.pomodoros_done || 0;
  if (!n) return '';
  const left = Math.max(0, n - done);
  const title = `${done} of ${n} pomodoro${n === 1 ? '' : 's'} done today${left ? ` · ${left} left` : ''}`;
  const marks = n <= 8 ? Array.from({ length: n }, (_, k) => `<span class="pomo-pip ${k < done ? 'on' : ''}"></span>`).join('')
    : `<span class="hud-num text-[11px] text-lilac-700">${done}/${n}</span>`;
  return `<span class="pomo inline-flex items-center gap-[3px]" title="${title}" aria-label="${title}">${marks}${done > n && n <= 8 ? `<span class="hud-num text-[10px] text-mint-700">+${done - n}</span>` : ''}</span>`;
}

// Under the agent's name: idle, or what it's doing right now (its current tool in the chat, or a scheduled task).
function agentStatus(found, v) {
  if (!found) return 'Offline: Claude Code wasn’t found';
  if (v?.chat_running) return `<span class="text-peach-700">${esc(v.chat_doing || 'Thinking')}…</span>`;
  if (v?.agent_work_running) return `<span class="text-peach-700">Running ${v.agent_work_running === 1 ? 'a scheduled task' : `${v.agent_work_running} scheduled tasks`}…</span>`;
  return 'Idle';
}

// The agent's light: working (orange, blinking), ready (green) or unavailable (red).
function agentLed(profile, running) {
  if (!profile.claude_found) return { cls: 'text-blush-300', title: 'Claude Code wasn’t found', attr: 'data-hud-agent-led' };
  return running ? { cls: 'text-peach-300 live', title: 'Your agent is working', attr: 'data-hud-agent-led' }
    : { cls: 'text-mint-300', title: 'Your agent is ready', attr: 'data-hud-agent-led' };
}

function ring(p, size = 96, stroke = 9) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" class="-rotate-90 hud-glow">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="rgb(var(--c-slate-200))" stroke-width="${stroke}"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="url(#hud-grad)" stroke-width="${stroke}" stroke-linecap="round"
      stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - p)}" class="hud-ring"/>
    <defs><linearGradient id="hud-grad"><stop offset="0" stop-color="rgb(var(--c-blush-300))"/><stop offset="1" stop-color="rgb(var(--c-baby-300))"/></linearGradient></defs>
  </svg>`;
}

function vitalsPanel(projects, today, v) {
  const done = projects.reduce((s, p) => s + p.leaves_done, 0), total = projects.reduce((s, p) => s + p.leaves_total, 0);
  const p = total ? done / total : 0;
  const n = key => today.groups.find(g => g.key === key)?.items.length || 0;
  const stat = (key, label) => `<div class="rounded-xl px-2 py-1 ${TODAY_STYLE[key]}"><p class="hud-num text-base font-extrabold leading-none">${n(key)}</p><p class="text-[9px] font-bold uppercase tracking-wide mt-0.5">${label}</p></div>`;
  return panel('Status', `
    <div class="flex items-center gap-4">
      <div class="relative">${ring(p, 64, 6)}<span class="absolute inset-0 flex flex-col items-center justify-center"><span class="hud-num text-base font-extrabold text-slate-800">${pct(p)}%</span><span class="text-[9px] text-slate-400">done</span></span></div>
      <div class="text-sm text-slate-500"><p><span class="hud-num font-bold text-slate-800">${done}</span>/${total} tasks</p><p><span class="hud-num font-bold text-slate-800">${projects.length}</span> projects</p></div>
    </div>
    <div class="mt-2 grid grid-cols-3 gap-1">
      ${stat('overdue', 'Overdue')}${stat('due_soon', 'Due soon')}${stat('in_progress', 'Started')}
      ${stat('ready', 'Ready')}${stat('in_review', 'Review')}${stat('blocked', 'Blocked')}
    </div>
    ${v.review_due ? `<a href="#/review" class="mt-3 block w-full rounded-xl bg-lilac-100 text-lilac-700 px-3 py-1.5 text-center text-xs font-bold hover:bg-lilac-200">Time for your weekly review →</a>` : ''}
    ${v.pending ? `<button data-hud-proposals class="mt-3 w-full rounded-xl bg-blush-100 text-blush-700 px-3 py-1.5 text-xs font-bold hover:bg-blush-200">🤖 ${v.pending} agent change${v.pending > 1 ? 's' : ''} waiting for your OK</button>` : ''}`,
  { cls: '' });
}

// Today's tasks: the suggested focus, then everything else due today, with one-click Start / Done.
function tasksPanel(today, projects) {
  const groupOf = id => today.groups.find(g => g.items.some(i => i.id === id))?.key;
  const todayIso = today.date;
  // Picked for today (by your agent, pick_todays_todos): exactly those, in its order, including to-dos written
  // just for today that aren't in any project. Otherwise automatic.
  const pk = today.picked;
  const dueToday = pk ? [] : today.groups.flatMap(g => g.items).filter(i => i.due_date === todayIso && !today.focus.some(f => f.id === i.id));
  const list = pk ? pk.items.map(it => it.text != null ? it : today.focus.find(f => f.id === it.block_id)).filter(Boolean)
    : [...today.focus, ...dueToday];
  const left = list.filter(i => !i.done).length;
  const focusId = hud.timer.task?.id;
  // A to-do written just for today: a checkbox and its text, ticked ones stay (struck through) so they can be un-ticked.
  const ownRow = i => `<li class="flex items-start gap-2.5 rounded-xl px-2 py-2 hover:bg-lavender-50/70">
      <button data-hud-tick="${i.index}" data-done="${i.done ? 1 : ''}" class="mt-0.5 w-5 h-5 shrink-0 rounded-md border-2 ${i.done ? 'border-mint-300 bg-mint-100' : 'border-lavender-300 hover:bg-mint-100 hover:border-mint-300'} flex items-center justify-center text-[11px] text-mint-700" title="${i.done ? 'Not done yet' : 'Mark done'}">${i.done ? '✓' : ''}</button>
      <span class="min-w-0 flex-1">
        <span class="block font-semibold break-words ${i.done ? 'line-through text-slate-400' : 'text-slate-800'}">${esc(i.text)}</span>
        <span class="block text-xs text-slate-400">Just for today</span>
      </span>
    </li>`;
  const row = i => {
    if (i.text != null) return ownRow(i);
    const key = groupOf(i.id), proj = projects.find(p => p.id === i.project_id);
    const startKey = proj && startStage(proj);
    const canStart = startKey && !i.locked && stageIndex(proj, i.status) < stageIndex(proj, startKey);
    return `<li class="group flex items-start gap-2.5 rounded-xl px-2 py-2 hover:bg-lavender-50/70 cursor-grab ${focusId === i.id ? 'hud-task-on' : ''}" data-task="${i.id}" data-hud-drag="${i.id}" draggable="true" title="Drag onto the focus timer to work on it">
      <button data-hud-done="${i.id}" class="mt-0.5 w-5 h-5 shrink-0 rounded-md border-2 border-lavender-300 hover:bg-mint-100 hover:border-mint-300 flex items-center justify-center text-[11px] text-mint-700" title="Mark done"></button>
      <a href="#/b/${i.id}" class="min-w-0 flex-1">
        <span class="block font-semibold text-slate-800 truncate">${i.priority === 3 ? `<span class="text-blush-700" title="High priority">${ico('flag')}</span> ` : ''}${esc(i.title)}</span>
        <span class="flex items-center gap-2 min-w-0 text-xs text-slate-400"><span class="truncate">${i.repeat_text ? `<span title="${esc(i.repeat_text)}">🔁</span> ` : ''}${esc(i.project)}${i.path ? ' › ' + esc(i.path) : ''} · ${esc(i.phase)}</span>${pomoPips(i)}</span>
      </a>
      <span class="shrink-0 flex items-center gap-1">
        ${key ? `<span class="rounded-full px-2 py-0.5 text-[10px] font-bold ${TODAY_STYLE[key]}">${TODAY_SHORT[key]}</span>` : ''}
        ${dueBadge(i.due_date, false, i.due_time)}
        ${canStart ? `<button data-hud-start="${i.id}" class="rounded-full border border-peach-200 px-2 py-0.5 text-[10px] font-bold text-peach-700 hover:bg-peach-50" title="Move it to “${esc(stageInfo[startKey]?.label)}”">▶ Start</button>` : ''}
      </span>
    </li>`;
  };
  const counts = today.groups.reduce((s, g) => s + g.items.length, 0);
  const view = todayView();
  const tabs = `<span class="flex items-center gap-2"><span class="flex rounded-full border border-lavender-200 overflow-hidden text-[10px] font-bold uppercase tracking-wider" role="group" aria-label="Show">
      ${[['plan', 'Plan'], ['todos', 'To-dos']].map(([k, l]) => `<button data-hud-tview="${k}" class="px-2 py-0.5 ${view === k ? 'bg-lavender-700 text-white' : 'text-slate-500 hover:text-lavender-700'}">${l}</button>`).join('')}</span>
    <a href="#/projects" class="text-[11px] font-semibold text-lavender-700 hover:underline">All →</a></span>`;
  if (view === 'plan') return panel('Today', planHTML(), { grow: true, cls: 'hud-grow-more', count: hud.plan ? `${hud.plan.items.filter(i => i.kind === 'todo').length} planned` : 'no plan', right: tabs });
  const who = pk && (pk.by === 'you' ? 'you' : chat?.agentName || 'your agent');
  const pickBar = pk ? `<div class="shrink-0 -mx-2 mb-1 flex items-center gap-2 rounded-xl bg-lavender-50/70 px-2 py-1.5 text-xs">
      <span class="min-w-0 flex-1 truncate text-lavender-800" title="${esc(pk.note || '')}"><b>✦ Picked by ${esc(who)}</b>${pk.note ? ` · <span class="text-slate-500">${esc(pk.note)}</span>` : ''}</span>
      <button data-hud-picks-auto class="shrink-0 font-semibold text-slate-500 hover:text-lavender-700" title="Show the automatic list again (overdue, due soon, started, ready)">Automatic</button>
    </div>` : '';
  return panel('Today', `${pickBar}
    ${left ? `<ul class="hud-scroll scroll-thin -mx-2">${list.map(row).join('')}</ul>`
      : `<p class="text-sm text-slate-500 py-4 text-center">${pk ? `All ${pk.count} picked to-do${pk.count === 1 ? '' : 's'} done. 🎉` : counts ? 'Nothing you can start right now: everything open is waiting on something.' : projects.length ? 'Nothing open. 🎉' : 'No projects yet.'}</p>`}
    ${projects.length ? `
      <form data-hud-add class="shrink-0 mt-2 flex flex-wrap items-center gap-2 border-t border-lavender-100 pt-3">
        <input name="title" required maxlength="200" autocomplete="off" placeholder="+ Quick add a to-do…" class="flex-1 min-w-[10rem] rounded-full border border-lavender-200 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-lavender-300">
        <select name="project" class="rounded-full border border-lavender-200 bg-white px-2 py-1.5 text-xs text-slate-600 max-w-[10rem]" aria-label="Project">
          ${projects.map(p => `<option value="${p.id}" ${String(p.id) === localGet('nestwork.hudProject') ? 'selected' : ''}>${esc(p.title)}</option>`).join('')}
        </select>
        <button class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-bold px-3 py-1.5">Add</button>
      </form>` : '<button data-hud-newproj class="w-full rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-sm font-bold px-4 py-2">+ Create your first project</button>'}`,
  { grow: true, cls: 'hud-grow-more', count: `${left} to-do${left === 1 ? '' : 's'}`, right: tabs });
}

// ---------------------------------------------------------------- plan my day (Today panel → Plan)
// Today's plan in order with start times: the one happening now is lit, finished to-dos are ticked, ▶ puts a
// to-do on the focus timer. "Plan my day with my agent" asks the agent in the chat (it reads the candidates
// and your calendar, proposes, and saves what you agree on); "Quick plan" lines up the suggested to-dos from
// now without AI. Server side: nestwork/plan.py.
const todayView = () => localGet('nestwork.todayView') || (hud.plan ? 'plan' : 'todos');
const PLAN_ASK = 'Plan my day: use get_day_plan to see my candidates (and check my calendar if you can), propose a schedule with times and short breaks, and save it with set_day_plan once I say it’s OK.';
function planHTML() {
  const p = hud.plan;
  if (!p) return `
    <div class="flex-1 flex flex-col items-center justify-center text-center gap-3 py-4">
      <p class="text-sm text-slate-500">No plan for today yet. Your agent can fit your to-dos around your calendar, or line them up yourself in one click.</p>
      <button data-hud-plan-agent class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-sm font-bold px-4 py-2">✦ Plan my day with my agent</button>
      <button data-hud-plan-quick class="text-xs font-semibold text-lavender-700 hover:underline" title="The suggested to-dos from the next quarter hour, high priority first, until your daily focus goal; no AI">Quick plan (no AI)</button>
    </div>`;
  const now = new Date(), hm = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
  const end = it => { const [h, m] = it.start.split(':').map(Number), t = h * 60 + m + it.minutes; return `${pad(Math.floor(t / 60) % 24)}:${pad(t % 60)}`; };
  const row = it => {
    const isNow = it.start <= hm && hm < end(it), past = end(it) <= hm;
    const dim = it.done || it.gone || (past && it.kind !== 'todo');
    return `<li class="flex items-center gap-2.5 rounded-xl px-2 py-1.5 ${isNow ? 'bg-lavender-50 ring-1 ring-lavender-300' : ''} ${dim ? 'opacity-50' : ''}" ${it.block_id ? `data-task="${it.block_id}"` : ''}>
      <span class="w-11 shrink-0 hud-num text-xs ${isNow ? 'text-lavender-800 font-bold' : 'text-slate-500'}">${it.start}</span>
      <span class="min-w-0 flex-1">
        <span class="block truncate text-sm ${it.kind === 'break' ? 'text-slate-400 italic' : 'font-semibold text-slate-800'} ${it.done ? 'line-through' : ''}">${it.kind === 'event' ? ico('bell') + ' ' : ''}${it.block_id ? `<a href="#/b/${it.block_id}" class="hover:text-lavender-800">${esc(it.title)}</a>` : esc(it.title)}</span>
        <span class="block truncate text-[11px] text-slate-400">${it.minutes} min${it.project ? ` · ${esc(it.project)}` : ''}${isNow ? ' · now' : ''}${it.gone ? ' · deleted' : ''}</span>
      </span>
      ${it.block_id && !it.done && !it.gone ? `<button data-hud-plan-focus="${it.block_id}" class="shrink-0 rounded-full border border-lilac-200 px-2 py-0.5 text-[10px] font-bold text-lilac-700 hover:bg-lilac-50" title="Put it on the focus timer">▶ Focus</button>` : it.done ? '<span class="shrink-0 text-mint-700 text-sm">✓</span>' : ''}
    </li>`;
  };
  return `
    <ul class="hud-scroll scroll-thin -mx-2 space-y-0.5">${p.items.map(row).join('')}</ul>
    <div class="shrink-0 mt-2 flex flex-wrap items-center gap-2 border-t border-lavender-100 pt-2 text-xs">
      <span class="text-slate-400">${p.made_by === 'you' ? 'Planned by you' : `Planned with ${esc(p.made_by)}`}</span>
      <button data-hud-plan-agent class="ml-auto font-semibold text-lavender-700 hover:underline">✦ Re-plan with my agent</button>
      <button data-hud-plan-clear class="text-slate-500 hover:text-blush-700">Clear</button>
    </div>`;
}
function bindPlan($) {
  app.querySelectorAll('[data-hud-tview]').forEach(b => b.onclick = () => { localSet('nestwork.todayView', b.dataset.hudTview); renderCurrent(); });
  app.querySelectorAll('[data-hud-plan-agent]').forEach(b => b.onclick = () => { setChatOpen(true, { focus: false }); sendMessage(PLAN_ASK); });
  $('[data-hud-plan-quick]')?.addEventListener('click', async () => {
    const r = await attempt(() => api('POST', '/api/plan/quick'));
    if (r) { hud.plan = r.plan; localSet('nestwork.todayView', 'plan'); toast(`Planned ${r.plan.items.filter(i => i.kind === 'todo').length} to-dos from ${r.plan.items[0].start}. ${UNDO_KEY} to undo.`, 'success'); renderCurrent(); }
  });
  $('[data-hud-plan-clear]')?.addEventListener('click', async () => {
    if (await attempt(() => api('DELETE', '/api/plan'))) { hud.plan = null; toast(`Cleared today’s plan. ${UNDO_KEY} brings it back.`, 'success'); renderCurrent(); }
  });
  app.querySelectorAll('[data-hud-plan-focus]').forEach(b => b.onclick = () => setTimerTask(Number(b.dataset.hudPlanFocus)));
  $('[data-hud-picks-auto]')?.addEventListener('click', async () => {
    if (await attempt(() => api('DELETE', '/api/today/picks'))) { toast(`Back to the automatic to-dos. ${UNDO_KEY} brings the picks back.`, 'success'); renderCurrent(); }
  });
}

function startStage(p) {
  const s = p.stages.find(s => s.role === 'active') || p.stages[1];
  return s && s !== p.stages[p.stages.length - 1] ? s.key : null;
}
const stageIndex = (p, key) => p.stages.findIndex(s => s.key === key);

// Reminders and timed due items in the next days, soonest first.
function upNextPanel(upcoming, today) {
  const seen = new Set();
  const items = upcoming.filter(u => {
    const k = u.kind === 'due' ? `d${u.block_id}` : `c${u.id}`;   // one line per to-do, its next reminder
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(0, 12);
  const overdue = today.groups.find(g => g.key === 'overdue')?.items || [];
  return panel('Up next', `
    ${overdue.length ? `<a href="#/b/${overdue[0].id}" class="shrink-0 mb-2 flex items-center gap-2 rounded-xl bg-blush-50 border border-blush-200 px-3 py-1.5 text-xs font-bold text-blush-700">⚠ ${overdue.length} overdue · ${esc(overdue[0].title)}${overdue.length > 1 ? ' and more' : ''}</a>` : ''}
    ${items.length ? `<ol class="hud-scroll scroll-thin relative border-l-2 border-lavender-100 ml-1.5 space-y-2.5">
      ${items.map(u => {
        const at = new Date(u.at);
        return `<li class="pl-4 relative">
          <span class="absolute -left-[7px] top-1.5 w-3 h-3 rounded-full ring-2 ring-white ${u.kind === 'due' ? 'bg-butter-300' : 'bg-lilac-300'}"></span>
          <p class="text-[11px] font-bold text-slate-400"><span class="hud-num">${at.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</span> · <span data-hud-rel="${at.getTime()}">${relTime(at)}</span></p>
          ${u.block_id ? `<a href="#/b/${u.block_id}" class="block text-sm font-semibold text-slate-700 truncate hover:text-lavender-800">` : '<p class="text-sm font-semibold text-slate-700 truncate">'}⏰ ${esc(u.kind === 'due' ? u.title : (u.text || u.title || 'Reminder'))}${u.block_id ? '</a>' : '</p>'}
          <p class="text-[11px] text-slate-400 truncate">${u.kind === 'due' ? `${esc(u.project)} · ${esc(u.choice)}` : `set by ${esc(u.created_by === 'agent' ? 'your agent' : u.created_by || 'you')}`}</p>
        </li>`;
      }).join('')}</ol>`
      : '<p class="text-sm text-slate-500 py-3">No reminders this week. Ask your agent: “remind me Friday at 9 to…”.</p>'}`,
  { grow: true, count: `${items.length} in 7 days`, led: overdue.length ? { cls: 'text-blush-300', title: `${overdue.length} overdue` } : null });
}

// The focus timer is the HUD's centerpiece: a big ring and clock, big controls, and what it's on.
function timerPanel() {
  const t = hud.timer, sw = isStopwatch();
  const state = sw ? (t.swFrom ? 'running' : swMs() > 0 ? 'paused' : 'ready') : t.endsAt ? 'running' : t.left < modeLength() ? 'paused' : 'ready';
  const led = { running: { cls: 'text-mint-300 live', title: 'The timer is running' }, paused: { cls: 'text-butter-300', title: 'The timer is paused' } }[state];
  const cfg = timerSettings();
  return panel('Focus timer', `
    <div class="flex flex-wrap items-center justify-center gap-x-6 gap-y-3">
      <div class="relative shrink-0">${ring(sw ? swRing() : 1, 176, 10).replace('class="hud-ring"', 'class="hud-ring" data-hud-timer-ring')}
        <svg class="timer-orbit absolute -inset-[10px] w-[196px] h-[196px]" viewBox="0 0 196 196" aria-hidden="true">
          <circle cx="98" cy="98" r="95" fill="none" stroke="rgb(var(--c-lilac-300) / .4)" stroke-width="1" stroke-dasharray="1 7"/>
          <circle class="orbit-dot" cx="98" cy="3" r="3" fill="rgb(var(--c-lilac-300))"/>
          <circle cx="98" cy="193" r="1.6" fill="rgb(var(--c-lavender-300))"/>
        </svg>
        <span class="absolute inset-0 flex flex-col items-center justify-center">
          <span data-hud-timer class="hud-num ${sw && swMs() >= 3600e3 ? 'text-4xl' : 'text-5xl'} font-extrabold text-slate-900 leading-none">${sw ? fmtElapsed(swMs()) : fmtClock(timerLeft())}</span>
          <span class="mt-1.5 text-xs font-bold uppercase tracking-[.2em] ${sw || t.mode === 'focus' ? 'text-lilac-700' : 'text-mint-700'}">${sw ? 'Stopwatch' : MODE_LABEL[t.mode]}</span>
        </span>
      </div>
      <div class="min-w-[13rem] flex-1 space-y-2">
        ${styleSwitchHTML()}
        ${sw ? stopwatchControlsHTML() : `
        <button data-hud-timer-toggle class="w-full rounded-full ${t.endsAt ? 'bg-peach-100 text-peach-700 hover:bg-peach-200 border border-peach-300' : 'bg-lavender-700 text-white hover:bg-lavender-800'} text-base font-bold px-4 py-2.5">${t.endsAt ? '❚❚ Pause' : '▶ Start'} <kbd class="hud-kbd ml-1 opacity-70">F</kbd></button>
        <div class="flex gap-2">
          <button data-hud-timer-reset class="flex-1 rounded-full border border-lavender-200 text-sm text-slate-600 px-2 py-1.5 hover:bg-lavender-50">Reset</button>
          <button data-hud-timer-skip class="flex-1 rounded-full border border-lavender-200 text-sm text-slate-600 px-2 py-1.5 hover:bg-lavender-50">${t.mode === 'focus' ? (nextBreak() === 'long' ? 'Long break' : 'Break') : 'Focus'} ⇥</button>
        </div>`}
        <div>${timerTaskHTML()}</div>
        <p class="text-[11px] text-slate-400"><span class="hud-num">${t.sessions || 0}</span> focus session${t.sessions === 1 ? '' : 's'} today · ${sw ? 'counts up until you finish' : `${cfg.focus}/${cfg.short} min`}${cfg.sound ? '' : ' · 🔇'}</p>
      </div>
    </div>`,
  { cls: `hud-hero ${t.endsAt || t.swFrom ? 'is-running' : ''}`, count: state, led, attrs: 'data-hud-drop', right: '<button data-hud-timer-settings class="text-xs text-slate-400 hover:text-lavender-700" title="Timer settings: lengths, sounds, auto-start" aria-label="Timer settings">⚙</button>' });
}

// ---------------------------------------------------------------- Claude usage (from Claude Code's /usage)
function usagePanel() {
  return panel('Claude usage', `<div data-hud-usage class="space-y-2">${usageHTML(hud.usage)}</div>`,
    { right: `<span class="flex items-center gap-1.5 text-[10px] text-slate-400" title="Read from Claude Code’s /usage"><span data-hud-usage-ago>${hud.usage ? agoText(hud.usage.checked_at * 1000) : ''}</span><button data-hud-usage-refresh class="text-xs hover:text-lavender-700" title="Check again now" aria-label="Check Claude usage again">↻</button></span>` });
}
function usageHTML(u) {
  if (!u) return '<p class="text-xs text-slate-400">Checking with Claude Code…</p>';
  if (!u.limits.length) return `<p class="text-xs text-blush-700">${esc(u.error || 'No usage limits were reported.')}</p>`;
  const name = l => l.key === 'session' ? '5-hour session' : l.key === 'week' ? 'Week · all models' : l.label.replace(/^Current week/, 'Week').replace(/^Current /, '');
  const tone = p => p >= 90 ? 'blush' : p >= 70 ? 'butter' : 'lavender';
  return u.limits.map(l => `
    <div title="${esc(l.label)}: ${l.used_percent}% used${l.resets ? ` · resets ${esc(l.resets)}` : ''}">
      <div class="flex items-baseline gap-2 text-xs min-w-0"><span class="shrink-0 font-semibold text-slate-700">${esc(name(l))}</span><span class="flex-1 min-w-0 truncate text-[10px] text-slate-400">${l.resets ? `resets ${esc(l.resets)}` : ''}</span><span class="shrink-0 hud-num font-bold text-${tone(l.used_percent)}-700">${Math.round(l.used_percent)}%</span></div>
      <div class="mt-1 h-1.5 bg-slate-200/60 overflow-hidden"><div class="h-1.5 bg-${tone(l.used_percent)}-300 shadow-[0_0_calc(var(--glow)*8px)_currentColor] text-${tone(l.used_percent)}-300 transition-[width] duration-700" style="width:${Math.min(100, l.used_percent)}%"></div></div>
    </div>`).join('');
}
async function loadUsage(force = false) {
  hud.usageAt = Date.now();
  try { hud.usage = await api('GET', `/api/claude/usage${force ? '?refresh=true' : ''}`); }
  catch (e) { hud.usage = { limits: [], error: e.message, checked_at: Date.now() / 1000 }; }
  const el = app.querySelector('[data-hud-usage]');
  if (el) { el.innerHTML = usageHTML(hud.usage); if (force) animate(el, 'fade-in'); }
  app.querySelectorAll('[data-hud-usage-ago]').forEach(a => { a.textContent = agoText(hud.usage.checked_at * 1000); });
}

// Below the timer: rate the session that just ended, what this session is on, or pick what's next.
const taskHref = task => task?.kind === 'block' || task?.kind === 'project' ? `#/b/${task.id}` : null;
function taskValue(task) {
  if (!task) return '';
  if (task.kind === 'other') return task.label ? `l:${task.title}` : 'o:';
  return `${task.kind === 'project' ? 'p' : 'b'}:${task.id}`;
}
// A value from the task list → a task (or null).
function taskFromValue(v) {
  if (!v) return null;
  const kind = v.slice(0, 1), rest = v.slice(2);
  if (kind === 'o') return { kind: 'other', id: null, title: '' };
  if (kind === 'l') return { kind: 'other', id: null, title: rest, label: true };
  const id = Number(rest), item = taskItem(id), proj = hud.projects.find(p => p.id === id);
  return kind === 'p' ? { kind: 'project', id, title: proj?.title || '', project: proj?.title || '' }
    : { kind: 'block', id, title: item?.title || '', project: item?.project || '' };
}
function timerTaskHTML() {
  const t = hud.timer;
  if (t.naming) return namingHTML();
  if (t.rateId) return `
    <div class="rounded-xl bg-butter-50 border border-butter-200 px-2.5 py-1.5" data-hud-rate>
      <p class="text-xs font-bold text-butter-700">How productive was that?</p>
      <div class="mt-1 flex items-center gap-1">
        ${[1, 2, 3, 4, 5].map(n => `<button data-hud-rate-n="${n}" class="w-7 h-7 rounded-lg bg-white border border-butter-200 text-sm font-bold text-slate-700 hover:bg-butter-100" title="${['', 'Barely', 'A bit', 'OK', 'Good', 'Great'][n]}">${n}</button>`).join('')}
        <button data-hud-rate-skip class="ml-auto text-[11px] text-slate-400 hover:text-slate-700">Skip</button>
      </div>
    </div>`;
  const name = task => { const h = taskHref(task); return h ? `<a href="${h}" class="font-semibold text-lilac-700 hover:underline">${esc(task.title)}</a>` : `<span class="font-semibold text-lilac-700">${esc(task.title)}</span>`; };
  const plan = t.task?.kind === 'block' ? pomoPips(taskItem(t.task.id)) : '';
  if (t.sessionId && !t.task && isStopwatch()) return '<p class="text-xs text-slate-500">◎ No name yet: you’ll say what it was when you finish.</p>';
  if (t.sessionId && t.task) return `<p class="flex items-center gap-2 text-xs text-slate-500 min-w-0"><span class="truncate">◎ On: ${name(t.task)}${t.task.project && t.task.project !== t.task.title ? ` <span class="text-slate-400">· ${esc(t.task.project)}</span>` : ''}</span>${plan}</p>`;
  if (t.mode !== 'focus') return `<p class="text-xs text-slate-500 truncate">☕ Break: step away from the screen.${t.task ? ` Next: ${name(t.task)}` : ''}</p>`;
  return `
    <label class="sr-only" for="hud-task">What are you working on?</label>
    ${taskSelectHTML(t.task, isStopwatch() ? 'Optional: what it’s for (or name it at the end)' : 'Drag a to-do here, or pick one', !isStopwatch())}
    ${plan ? `<p class="mt-1 flex items-center gap-2 text-[11px] text-slate-400">Today’s plan ${plan}</p>` : ''}
    ${t.task?.kind === 'other' && !t.task.label ? otherInputHTML(t.task.title) : ''}`;
}
// The "what is it for" list: suggested to-dos, every project with its open to-dos, your own labels, something else.
function taskSelectHTML(task, placeholder, warn = true, attr = 'data-hud-task') {
  const today = hud.lastToday, open = today ? today.groups.filter(g => g.key !== 'blocked').flatMap(g => g.items) : [];
  const byProject = new Map(hud.projects.map(p => [p.id, { p, items: [] }]));
  open.forEach(i => byProject.get(i.project_id)?.items.push(i));
  const val = taskValue(task);
  const opt = (v, label) => `<option value="${esc(v)}" ${v === val ? 'selected' : ''}>${esc(label)}</option>`;
  const labels = (hud.labels || []).filter(l => !(task?.kind === 'other' && task.title === l && val === 'o:'));
  return `<select id="hud-task" ${attr} class="w-full rounded-lg border ${task || !warn ? 'border-lavender-200' : 'border-butter-300'} bg-white py-1 pl-2 pr-7 text-xs text-slate-700">
      <option value="" ${val ? '' : 'selected'}>${esc(placeholder)}</option>
      ${today?.focus.length ? `<optgroup label="Suggested now">${today.focus.map(i => opt(`b:${i.id}`, i.title)).join('')}</optgroup>` : ''}
      ${[...byProject.values()].map(({ p, items }) => `<optgroup label="${esc(p.title)}">${opt(`p:${p.id}`, `The whole project: ${p.title}`)}${items.map(i => opt(`b:${i.id}`, i.path ? `${i.title} · ${i.path}` : i.title)).join('')}</optgroup>`).join('')}
      ${labels.length ? `<optgroup label="Your labels">${labels.map(l => opt(`l:${l}`, l)).join('')}</optgroup>` : ''}
      <optgroup label="Other">${opt('o:', 'Something else (type a name)…')}</optgroup>
    </select>`;
}
function otherInputHTML(value, attr = 'data-hud-task-other') {
  return `<input ${attr} value="${esc(value)}" maxlength="200" list="hud-labels" placeholder="e.g. Emails, reading, admin" class="mt-1 w-full rounded-lg border border-lavender-200 bg-white px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-lavender-300">
    <datalist id="hud-labels">${(hud.labels || []).map(l => `<option value="${esc(l)}">`).join('')}</datalist>`;
}

// The next 7 days with how much is due on each.
function weekPanel(today) {
  const all = today.groups.flatMap(g => g.items);
  const start = new Date(today.date + 'T00:00');
  const days = Array.from({ length: 7 }, (_, k) => { const d = new Date(start); d.setDate(d.getDate() + k); return d; });
  const dueThisWeek = all.filter(i => days.some(d => i.due_date === isoDay(d))).length;
  return panel('Week ahead', `
    <div class="grid grid-cols-7 gap-1.5">
      ${days.map((d, k) => {
        const due = all.filter(i => i.due_date === isoDay(d));
        return `<div class="rounded-xl text-center py-2 ${k === 0 ? 'hud-today ' : ''}${k === 0 ? 'bg-lavender-700 text-white shadow-[0_0_calc(var(--glow)*16px)_-2px_rgb(var(--c-lavender-300)/.8)]' : 'bg-lavender-50 text-slate-600'}" title="${due.map(i => esc(i.title)).join('\n') || 'Nothing due'}">
          <p class="text-[10px] font-bold uppercase">${d.toLocaleDateString([], { weekday: 'short' })}</p>
          <p class="hud-num text-lg font-extrabold leading-tight">${d.getDate()}</p>
          <p class="h-3 flex justify-center gap-0.5 mt-0.5">${due.slice(0, 4).map(() => `<span class="w-1.5 h-1.5 rounded-full ${k === 0 ? 'bg-butter-200' : 'bg-peach-300'}"></span>`).join('')}</p>
        </div>`;
      }).join('')}
    </div>
    ${(() => {
      const next = all.filter(i => i.due_date && i.due_date >= today.date).sort((a, b) => (a.due_date + (a.due_time || '')).localeCompare(b.due_date + (b.due_time || '')))[0];
      return next ? `<p class="mt-3 text-xs text-slate-500 truncate">Next deadline: <a href="#/b/${next.id}" class="font-semibold text-slate-700 hover:text-lavender-800">${esc(next.title)}</a> ${dueBadge(next.due_date, false, next.due_time)}</p>` : '<p class="mt-3 text-xs text-slate-400">No deadlines coming up.</p>';
    })()}`,
  { cls: '', count: `${dueThisWeek} due` });
}

function projectsPanel(projects) {
  return panel('Projects', projects.length ? `
    <ul class="hud-scroll scroll-thin space-y-2">
      ${projects.map(p => { const a = ACCENTS[p.id % ACCENTS.length]; return `
        <li><a href="#/b/${p.id}" class="block rounded-xl px-2 py-1 -mx-2 hover:bg-${a}-50">
          <div class="flex items-center justify-between gap-2 text-sm">
            <span class="flex items-center gap-2 min-w-0"><span class="w-2 h-2 shrink-0 rounded-full bg-${a}-300"></span><span class="font-bold text-slate-800 truncate">${esc(p.title)}</span></span>
            <span class="shrink-0 text-xs text-slate-400"><span class="text-baby-700 font-semibold">${p.ready_now} ready</span> · <span class="hud-num font-bold ${pct(p.progress) === 100 ? 'text-mint-700' : 'text-slate-600'}">${pct(p.progress)}%</span></span>
          </div>
          <div class="mt-1">${bar(p.progress, 'h-1.5', `hud${p.id}`)}</div>
        </a></li>`; }).join('')}
    </ul>`
    : '<p class="text-sm text-slate-500">No projects yet.</p>',
  { grow: true, count: projects.length, right: '<button data-hud-newproj class="text-[11px] font-semibold text-lavender-700 hover:underline">+ New project</button>' });
}

// ---------------------------------------------------------------- interactions
function bindHud() {
  const $ = s => app.querySelector(s);
  app.querySelectorAll('[data-hud-chat]').forEach(b => b.onclick = () => setChatOpen(!document.body.classList.contains('chat-open')));
  const ask = text => { if (!text.trim()) return; setChatOpen(true, { focus: false }); sendMessage(text); };
  $('[data-hud-ask]').onsubmit = e => { e.preventDefault(); const q = e.target.q; ask(q.value); q.value = ''; };
  $('[data-hud-proposals]')?.addEventListener('click', () => settingsModal('access'));
  app.querySelectorAll('[data-hud-newproj]').forEach(b => b.onclick = newProjectModal);

  app.querySelectorAll('[data-hud-done]').forEach(b => b.onclick = async () => {
    const id = Number(b.dataset.hudDone), item = taskItem(id);
    const p = hud.projects.find(p => p.id === item?.project_id);
    if (!p) return;
    b.textContent = '✓';
    const res = await attempt(() => api('POST', `/api/blocks/${id}/move`, { status: p.stages[p.stages.length - 1].key }));
    if (!res) { b.textContent = ''; return; }
    notify(res);
    const li = b.closest('li');
    await animateOut([li], 'poof', 220);
    toast(`Done: “${item.title}” 🎉`, 'success');
    renderCurrent();
  });
  app.querySelectorAll('[data-hud-tick]').forEach(b => b.onclick = async () => {
    const done = !b.dataset.done;
    b.textContent = done ? '✓' : '';
    if (!await attempt(() => api('POST', `/api/today/picks/${b.dataset.hudTick}/done`, { done }))) { b.textContent = done ? '' : '✓'; return; }
    if (done) toast(`Done 🎉 ${UNDO_KEY} to undo.`, 'success');
    renderCurrent();
  });
  app.querySelectorAll('[data-hud-start]').forEach(b => b.onclick = async () => {
    const id = Number(b.dataset.hudStart), item = taskItem(id);
    const p = hud.projects.find(p => p.id === item?.project_id);
    const res = p && await attempt(() => api('POST', `/api/blocks/${id}/move`, { status: startStage(p) }));
    if (res) { notify(res); renderCurrent(); }
  });
  // Drag a to-do from Today onto the focus timer to make it what the next session is for.
  const drop = $('[data-hud-drop]'), TYPE = 'text/x-nestwork-task';
  app.querySelectorAll('[data-hud-drag]').forEach(li => {
    li.addEventListener('dragstart', e => {
      e.dataTransfer.setData(TYPE, li.dataset.hudDrag);
      e.dataTransfer.effectAllowed = 'link';
      li.classList.add('dragging');
      drop.classList.add('hud-drop-ready');
    });
    li.addEventListener('dragend', () => { li.classList.remove('dragging'); drop.classList.remove('hud-drop-ready', 'hud-drop-on'); });
  });
  drop.addEventListener('dragover', e => {
    if (!e.dataTransfer.types.includes(TYPE)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'link';
    drop.classList.add('hud-drop-on');
  });
  drop.addEventListener('dragleave', e => { if (!drop.contains(e.relatedTarget)) drop.classList.remove('hud-drop-on'); });
  drop.addEventListener('drop', e => {
    const id = Number(e.dataTransfer.getData(TYPE));
    if (!id) return;
    e.preventDefault();
    drop.classList.remove('hud-drop-ready', 'hud-drop-on');
    setTimerTask(id);
  });
  bindTimer($);
  bindPlan($);
  $('[data-hud-usage-refresh]').onclick = () => loadUsage(true);

  $('[data-hud-add]')?.addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target, title = f.title.value.trim(), parent = Number(f.project.value);
    if (!title) return;
    localSet('nestwork.hudProject', String(parent));
    const res = await attempt(() => api('POST', '/api/blocks', { title, parent_id: parent }));
    if (res) {
      notify(res);
      toast(`Added “${title}”.`, 'success');
      f.title.value = '';
      f.title.blur();
      renderCurrent();
    }
  });
}
// The focus timer's controls: what it's for, rating, start/pause, reset, skip, settings, the stopwatch.
// Used on the HUD and on the phone's full-screen timer (#/timer).
function bindTimer($) {
  $('[data-hud-task]')?.addEventListener('change', e => {
    hud.timer.task = taskFromValue(e.target.value);
    saveTimer();
    renderCurrent().then(() => app.querySelector('[data-hud-task-other]')?.focus());
  });
  $('[data-hud-task-other]')?.addEventListener('input', e => { hud.timer.task.title = e.target.value; saveTimer(); });
  $('[data-hud-task-other]')?.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); timerToggle(); } });
  app.querySelectorAll('[data-hud-rate-n]').forEach(b => b.onclick = () => rateSession(Number(b.dataset.hudRateN)));
  $('[data-hud-rate-skip]')?.addEventListener('click', () => rateSession(null));
  $('[data-hud-timer-toggle]')?.addEventListener('click', timerToggle);
  bindStopwatch($);
  $('[data-hud-timer-settings]')?.addEventListener('click', () => settingsModal('timer'));
  $('[data-hud-timer-reset]')?.addEventListener('click', () => { endSession(false); Object.assign(hud.timer, { endsAt: null, left: modeLength() }); saveTimer(); renderCurrent(); });
  $('[data-hud-timer-skip]')?.addEventListener('click', () => switchMode(false));
}

// Make a to-do what the focus timer works on (it stays there session after session until its daily plan is done).
function setTimerTask(id, title = '', project = '') {
  if (hud.timer.sessionId) return toast('A focus session is running. Finish or reset it to switch tasks.', 'info');
  const item = taskItem(id);
  hud.timer.task = { kind: 'block', id, title: item?.title || title, project: item?.project || project };
  if (hud.timer.mode !== 'focus') Object.assign(hud.timer, { mode: 'focus', endsAt: null, left: modeLength('focus') });
  saveTimer();
  renderCurrent().then(() => animate(app.querySelector('[data-hud-drop]'), 'land'));
}

// After a finished session on a to-do: how many of its planned pomodoros are left today. At zero the
// timer lets go of it, so the next session is for whatever you drag in next.
async function checkDailyPlan(task) {
  const today = await api('GET', '/api/today').catch(() => null);
  if (!today) return;
  hud.lastToday = today;
  const item = today.groups.flatMap(g => g.items).find(i => i.id === task.id);
  if (!item?.pomodoros) return;
  const left = item.pomodoros - item.pomodoros_done, t = hud.timer;
  if (left > 0) toast(`${left} pomodoro${left === 1 ? '' : 's'} left today for “${item.title}”.`, 'info');
  else if (t.task?.kind === 'block' && t.task.id === task.id && !t.sessionId) {
    t.task = null;
    saveTimer();
    toast(`“${item.title}”: all ${item.pomodoros} pomodoros done today. Drag the next to-do onto the timer.`, 'success');
  }
  if (onHudPage()) renderCurrent();
}

function taskItem(id) {
  return hud.lastToday?.groups.flatMap(g => g.items).find(i => i.id === id);
}

// ---------------------------------------------------------------- focus timer (shared through the server)
// The running timer and its settings (Settings → ⏱ Focus timer) are saved on the server (nestwork/timer.py),
// so every device shows the same timer; this browser keeps a copy so it starts instantly and works offline.
const TIMER_DEFAULTS = {
  focus: 25, short: 5, long: 15, longEvery: 4,      // minutes; a long break after every N focus sessions (0 = never)
  autoBreak: false, autoFocus: false,                // start the next part by itself
  sound: true, repeat: 2,                            // which sounds and how loud: Settings → 🔊 Sounds
  warn: false,                                       // a soft tick one minute before the end
  notify: true,
};
const MODE_LABEL = { focus: 'Focus', short: 'Break', long: 'Long break' };

function timerSettings() {
  let saved = hud.timerCfg;
  if (!saved) { try { saved = JSON.parse(localGet('nestwork.timerSettings') || '{}') || {}; } catch { saved = {}; } }
  return { ...TIMER_DEFAULTS, ...saved };
}
function saveTimerSettings(patch) {
  const before = timerSettings(), next = { ...before, ...patch };
  hud.timerCfg = next;
  localSet('nestwork.timerSettings', JSON.stringify(next));
  api('PUT', '/api/timer/settings', { settings: next }).catch(() => { /* offline: kept here, sent next time */ });
  // A timer that isn't running picks up a new length straight away.
  const t = hud.timer;
  if (!t.endsAt && t.left === (before[t.mode] || before.short) * 60e3) { t.left = modeLength(); saveTimer(); }
  return next;
}

function loadTimer() {
  let t = null;
  try { t = JSON.parse(localGet('nestwork.hudTimer') || 'null'); } catch { /* corrupt: start over */ }
  return normalizeTimer(t);
}
function normalizeTimer(t) {
  t = { mode: 'focus', endsAt: null, left: null, task: null, sessions: 0, day: null, warned: false, ...(t || {}) };
  if (t.mode === 'break') t.mode = 'short';   // older saved timers
  if (t.task && !t.task.kind) t.task.kind = 'block';
  if (t.day !== isoDay(new Date())) { t.sessions = 0; t.day = isoDay(new Date()); }
  return t;
}
// Save locally at once and on the server right after (several saves in a row are sent once).
let timerPush = null, timerPushing = false;
function saveTimer() {
  localSet('nestwork.hudTimer', JSON.stringify(hud.timer));
  clearTimeout(timerPush);
  timerPushing = true;
  timerPush = setTimeout(async () => {
    try { hud.timerRev = (await api('PUT', '/api/timer', { state: hud.timer })).rev; } catch { /* offline: sent next save */ }
    timerPushing = false;
  }, 120);
}
// Load the shared timer: on start, and whenever another device changed it (the revision in /api/version moved).
async function syncTimer() {
  let r;
  try { r = await api('GET', '/api/timer'); } catch { return; }
  if (r.settings) { hud.timerCfg = r.settings; localSet('nestwork.timerSettings', JSON.stringify(r.settings)); }
  else if (localGet('nestwork.timerSettings')) saveTimerSettings({});      // first run: this browser's settings become the shared ones
  if (!r.state) { saveTimer(); return; }                                   // first run: share this browser's timer
  if (r.rev === hud.timerRev) return;
  const before = hud.timer;
  hud.timer = normalizeTimer(r.state);
  hud.timerRev = r.rev;
  localSet('nestwork.hudTimer', JSON.stringify(hud.timer));
  // Another device just ended a session or break as time ran out: play the cue here too.
  if (before.endsAt && before.endsAt !== lostClaim && hud.timer.mode !== before.mode && Math.abs(before.endsAt - Date.now()) < 8000) endCue(before.mode === 'focus');
  if (onHudPage()) renderCurrent();
  else if (location.hash.startsWith('#/focus')) window.onFocusLogged?.();
}
// (Our own save in flight also moves the revision: wait for it rather than reload what we just sent.)
window.onTimerRev = rev => { if (hud.timerRev !== undefined && rev !== hud.timerRev && !timerPushing) syncTimer(); };
hud.timer = loadTimer();
hud.timerRev = undefined;     // the server's revision of the timer we have; set by syncTimer
syncTimer().then(() => { hud.timerRev ??= 0; });
const modeLength = (mode = hud.timer.mode) => (timerSettings()[mode] || TIMER_DEFAULTS.short) * 60e3;
hud.timer.left ??= modeLength();
const timerLeft = () => hud.timer.endsAt ? Math.max(0, hud.timer.endsAt - Date.now()) : hud.timer.left;
// Which break comes after the current focus session.
function nextBreak() {
  const every = Number(timerSettings().longEvery);
  return every > 0 && ((hud.timer.sessions || 0) + 1) % every === 0 ? 'long' : 'short';
}
const onHudPage = () => location.hash === '' || location.hash === '#/' || location.hash.startsWith('#/timer');

async function timerToggle() {
  const t = hud.timer;
  sndCtx();
  if (isStopwatch()) return stopwatchToggle();
  if (t.endsAt) { t.left = timerLeft(); t.endsAt = null; saveTimer(); if (onHudPage()) renderCurrent(); return; }
  // A focus session needs to know what it's for: it's logged for the Focus page and your agent.
  if (t.mode === 'focus' && !t.sessionId) {
    if (!t.task || !t.task.title.trim()) {
      if (!onHudPage()) location.hash = '#/';
      toast('Pick what you’re working on first.', 'info');
      setTimeout(() => (app.querySelector('[data-hud-task-other]') || app.querySelector('[data-hud-task]'))?.focus(), 400);
      return;
    }
    const body = { planned_min: (t.left || modeLength()) / 60e3, title: t.task.title.trim(),
      block_id: t.task.kind === 'block' ? t.task.id : null, project_id: t.task.kind === 'project' ? t.task.id : null };
    const session = await attempt(() => api('POST', '/api/focus/start', body));
    if (!session) return;
    t.sessionId = session.id;
    t.task.project = session.project_title || t.task.project || '';
  }
  t.endsAt = Date.now() + (t.left || modeLength());
  t.warned = (t.left || modeLength()) <= 60e3;
  if (timerSettings().notify && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
  saveTimer();
  if (onHudPage()) renderCurrent();
}

// Close the running focus session on the server: finished (full length) or stopped (time so far).
function endSession(completed) {
  const t = hud.timer, sid = t.sessionId, task = t.task;
  if (!sid || isStopwatch()) return;
  const focusMin = completed ? modeLength('focus') / 60e3 : (modeLength('focus') - timerLeft()) / 60e3;
  t.sessionId = null;
  if (completed) t.rateId = sid;
  saveTimer();
  api('POST', `/api/focus/${sid}/finish`, { focus_min: Math.max(0, focusMin), completed })
    .then(() => { poll(true); window.onFocusLogged?.(); if (completed && task?.kind === 'block') checkDailyPlan(task); })
    .catch(e => toast(`Couldn’t save that focus session: ${e.message}`, 'error'));
}
async function rateSession(rating) {
  const sid = hud.timer.rateId;
  hud.timer.rateId = null;
  saveTimer();
  if (rating && sid) {
    if (await attempt(() => api('PATCH', `/api/focus/${sid}`, { rating }))) toast(`Rated ${rating}/5. See it on the Focus page.`, 'success');
  }
  renderCurrent();
}

function switchMode(finished) {
  const t = hud.timer, cfg = timerSettings(), wasFocus = t.mode === 'focus';
  if (wasFocus) endSession(finished);
  if (finished && wasFocus && t.day !== isoDay(new Date())) { t.sessions = 0; t.day = isoDay(new Date()); }
  const next = wasFocus ? nextBreak() : 'focus';
  if (finished && wasFocus) t.sessions = (t.sessions || 0) + 1;
  t.mode = next;
  t.left = modeLength();
  t.warned = false;
  // Auto-starting focus reuses the same task; without one, it waits for you to pick.
  const auto = finished && (next === 'focus' ? cfg.autoFocus && t.task?.title : cfg.autoBreak);
  t.endsAt = null;
  saveTimer();
  if (auto) {
    if (next === 'focus') timerToggle();
    else { t.endsAt = Date.now() + t.left; saveTimer(); }
  }
  if (finished) {
    const mins = Math.round(t.left / 60e3);
    const msg = next === 'focus'
      ? `Break’s over. ${auto ? 'Next focus session started.' : 'Ready for the next one?'}`
      : `Focus session done${t.task ? ` on “${t.task.title}”` : ''}. Take ${mins} minutes${next === 'long' ? ', you’ve earned a long break' : ''}. ☕`;
    endCue(wasFocus, msg);
  }
  if (onHudPage()) renderCurrent();
}

// ---------------------------------------------------------------- stopwatch (count-up)
// Counts up from 0 while running; pauses don't count. Finish saves it as a finished session (rated like a
// pomodoro). It can start without a name: Finish then asks what it was (a to-do, a project, one of your
// labels, or a new name). Discard throws the session away.
const isStopwatch = () => hud.timer.style === 'stopwatch';
const swMs = () => (hud.timer.swElapsed || 0) + (hud.timer.swFrom ? Date.now() - hud.timer.swFrom : 0);
const swRing = () => (swMs() % 3600e3) / 3600e3;     // fills once an hour

function styleSwitchHTML() {
  const t = hud.timer, busy = t.sessionId || t.naming || (isStopwatch() ? swMs() > 0 : t.endsAt || t.left < modeLength());
  const b = (k, label, title) => `<button data-hud-style="${k}" ${busy ? 'disabled' : ''} title="${title}" class="flex-1 px-2 py-1 text-[11px] font-bold uppercase tracking-wider ${(t.style || 'pomodoro') === k ? 'bg-lavender-700 text-white' : 'text-slate-500 hover:text-lavender-700'} disabled:cursor-not-allowed">${label}</button>`;
  return `<div class="flex rounded-full border border-lavender-200 overflow-hidden ${busy ? 'opacity-60' : ''}" role="group" aria-label="Timer mode"${busy ? ' title="Finish or reset the current session to switch"' : ''}>
    ${b('pomodoro', 'Pomodoro', 'Counts down a set length, then a break')}${b('stopwatch', 'Stopwatch', 'Counts up until you press Finish')}</div>`;
}

function stopwatchControlsHTML() {
  const t = hud.timer, ms = swMs();
  if (t.naming) return '';
  return `
    <button data-hud-timer-toggle class="w-full rounded-full ${t.swFrom ? 'bg-peach-100 text-peach-700 hover:bg-peach-200 border border-peach-300' : 'bg-lavender-700 text-white hover:bg-lavender-800'} text-base font-bold px-4 py-2.5">${t.swFrom ? '❚❚ Pause' : ms > 0 ? '▶ Resume' : '▶ Start'} <kbd class="hud-kbd ml-1 opacity-70">F</kbd></button>
    <div class="flex gap-2">
      <button data-hud-sw-discard ${ms > 0 ? '' : 'disabled'} class="flex-1 rounded-full border border-lavender-200 text-sm text-slate-600 px-2 py-1.5 hover:bg-lavender-50 disabled:opacity-40">Discard</button>
      <button data-hud-sw-finish ${ms >= 1000 ? '' : 'disabled'} class="flex-1 rounded-full border border-mint-300 bg-mint-50 text-sm font-bold text-mint-700 px-2 py-1.5 hover:bg-mint-100 disabled:opacity-40">✓ Finish</button>
    </div>`;
}

// After Finish on an unnamed stopwatch session: what was it?
function namingHTML() {
  const n = hud.timer.naming;
  return `
    <div class="rounded-xl bg-lavender-50 border border-lavender-200 px-2.5 py-2 space-y-1.5" data-hud-naming>
      <p class="text-xs font-bold text-lavender-800">What was this? <span class="font-normal text-slate-500">(${esc(fmtElapsed(n.ms))})</span></p>
      ${taskSelectHTML(n.task, 'Pick from the list…', true, 'data-hud-name-pick')}
      ${n.task?.kind === 'other' && !n.task.label ? otherInputHTML(n.task.title, 'data-hud-name-other') : ''}
      <div class="flex gap-2">
        <button data-hud-name-back class="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-white">Back</button>
        <button data-hud-name-save class="flex-1 rounded-full bg-lavender-700 text-white text-xs font-bold px-3 py-1 hover:bg-lavender-800">Save session</button>
      </div>
    </div>`;
}

async function stopwatchToggle() {
  const t = hud.timer;
  if (t.naming) return;
  if (t.swFrom) { t.swElapsed = swMs(); t.swFrom = null; saveTimer(); return renderCurrent(); }
  if (!t.sessionId) {
    const task = t.task?.title?.trim() ? t.task : null;
    const session = await attempt(() => api('POST', '/api/focus/start', { mode: 'stopwatch', title: task?.title.trim() || '',
      block_id: task?.kind === 'block' ? task.id : null, project_id: task?.kind === 'project' ? task.id : null }));
    if (!session) return;
    t.sessionId = session.id;
    t.task = task;
    t.swElapsed = 0;
  }
  t.swFrom = Date.now();
  saveTimer();
  renderCurrent();
}

function stopwatchFinish() {
  const t = hud.timer;
  t.swElapsed = swMs(); t.swFrom = null;
  if (t.task?.title?.trim()) return saveStopwatch(t.task);
  t.naming = { ms: t.swElapsed, task: null };
  saveTimer();
  renderCurrent().then(() => app.querySelector('[data-hud-name-pick]')?.focus());
}

async function saveStopwatch(task) {
  const t = hud.timer, sid = t.sessionId;
  const body = { focus_min: t.swElapsed / 60e3, completed: true, title: task.title.trim(),
    block_id: task.kind === 'block' ? task.id : null, project_id: task.kind === 'project' ? task.id : null };
  const r = await attempt(() => api('POST', `/api/focus/${sid}/finish`, body));
  if (!r) return;
  if (task.kind === 'other' && !(hud.labels || []).includes(task.title.trim())) hud.labels = [task.title.trim(), ...(hud.labels || [])];
  if (t.day !== isoDay(new Date())) { t.sessions = 0; t.day = isoDay(new Date()); }
  Object.assign(t, { sessionId: null, swElapsed: 0, swFrom: null, naming: null, rateId: r.session ? sid : null,
    sessions: (t.sessions || 0) + 1, task: task.kind === 'block' ? task : t.task });
  saveTimer();
  const cfg = timerSettings();
  if (cfg.sound) playKeyedSound('focus_end');
  toast(`Saved ${fmtMin(body.focus_min)} on “${task.title.trim()}”.`, 'success');
  poll(true); window.onFocusLogged?.();
  if (task.kind === 'block') checkDailyPlan(task);
  renderCurrent();
}

async function stopwatchDiscard() {
  const t = hud.timer, ms = swMs();
  if (ms > 5 * 60e3 && !confirm(`Throw away this ${fmtElapsed(ms)} session? It won’t be saved.`)) return;
  if (t.sessionId) await api('DELETE', `/api/focus/${t.sessionId}`).catch(() => {});
  Object.assign(t, { sessionId: null, swElapsed: 0, swFrom: null, naming: null });
  saveTimer();
  poll(true);
  renderCurrent();
}

function bindStopwatch($) {
  app.querySelectorAll('[data-hud-style]').forEach(b => b.onclick = () => {
    hud.timer.style = b.dataset.hudStyle;
    saveTimer();
    renderCurrent();
  });
  $('[data-hud-sw-finish]')?.addEventListener('click', stopwatchFinish);
  $('[data-hud-sw-discard]')?.addEventListener('click', stopwatchDiscard);
  const n = hud.timer.naming;
  if (!n) return;
  $('[data-hud-name-pick]')?.addEventListener('change', e => {
    n.task = taskFromValue(e.target.value);
    saveTimer();
    renderCurrent().then(() => app.querySelector('[data-hud-name-other]')?.focus());
  });
  $('[data-hud-name-other]')?.addEventListener('input', e => { n.task.title = e.target.value; saveTimer(); });
  $('[data-hud-name-other]')?.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('[data-hud-name-save]').click(); } });
  $('[data-hud-name-back]')?.addEventListener('click', () => { hud.timer.naming = null; saveTimer(); renderCurrent(); });
  $('[data-hud-name-save]')?.addEventListener('click', () => {
    if (!n.task?.title?.trim()) { toast('Pick what it was, or type a name.', 'info'); (app.querySelector('[data-hud-name-other]') || app.querySelector('[data-hud-name-pick]'))?.focus(); return; }
    saveStopwatch(n.task);
  });
}

// The sound, toast and desktop notification when a focus session or a break ends.
function endCue(wasFocus, msg = null) {
  const cfg = timerSettings();
  msg ??= wasFocus ? 'Focus session done. Time for a break. ☕' : 'Break’s over. Ready for the next one?';
  if (cfg.sound) playKeyedSound(wasFocus ? 'focus_end' : 'break_end', cfg.repeat);
  toast(msg, 'success');
  if (cfg.notify && 'Notification' in window && Notification.permission === 'granted') new Notification('Nestwork focus timer', { body: msg, tag: 'nestwork-timer', silent: cfg.sound });
}

// Runs every second on every page: the timer keeps going when you leave the HUD.
// When time runs out, the first device to claim it does the switch; the others reload it (and play the cue).
let timerClaiming = false, lostClaim = null;
function timerTick() {
  const t = hud.timer;
  if (!t.endsAt) return;
  const ms = timerLeft(), cfg = timerSettings();
  if (ms <= 0) {
    // Lost the claim for this ending already: wait for the winner's new state instead of trying again.
    if (timerClaiming || lostClaim === t.endsAt) return;
    timerClaiming = true;
    const end = t.endsAt;
    api('POST', '/api/timer/claim', { rev: hud.timerRev ?? 0 })
      .then(r => { hud.timerRev = r.rev; switchMode(true); })
      .catch(e => {
        if (String(e.message).includes('Another device')) { lostClaim = end; endCue(t.mode === 'focus'); setTimeout(syncTimer, 1500); }
        else switchMode(true);                                                // offline: do it here
      })
      .finally(() => { timerClaiming = false; });
    return;
  }
  if (!t.warned && ms <= 60e3) {
    t.warned = true;
    saveTimer();
    if (cfg.warn && cfg.sound && soundsOn()) playTick(snd.lib?.notifications.find(n => n.key === 'focus_end')?.volume ?? 0.7);
  }
}

// ---------------------------------------------------------------- one-minute warning tick
// The end-of-session sounds are notifications (sounds.js, Settings → 🔊 Sounds); this soft tick is the timer's own.
function playTick(volume = 0.7) {
  const ctx = sndCtx();
  if (!ctx) return;
  const out = ctx.createGain();
  out.gain.value = Math.min(1, volume);
  out.connect(ctx.destination);
  sndTone(ctx, out, { f: 1318.5, dur: 0.25, type: 'triangle', gain: 0.25 });
}

// ---------------------------------------------------------------- Settings → ⏱ Focus timer
function timerSettingsHTML() {
  const c = timerSettings();
  const num = (key, label, min, max, help = '') => `
    <label class="block">
      <span class="block text-xs font-semibold text-slate-600 mb-1">${label}</span>
      <span class="flex items-center gap-2"><input type="number" data-ts="${key}" value="${c[key]}" min="${min}" max="${max}" class="w-20 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-lavender-300"><span class="text-xs text-slate-400">${help}</span></span>
    </label>`;
  const check = (key, label, help) => `
    <label class="flex items-start gap-3 rounded-xl border border-slate-200 p-2.5 cursor-pointer hover:bg-slate-50">
      <input type="checkbox" data-ts="${key}" ${c[key] ? 'checked' : ''} class="mt-0.5 rounded text-lavender-700">
      <span><span class="block text-sm font-semibold">${label}</span><span class="block text-xs text-slate-500">${help}</span></span>
    </label>`;
  return `
    <div class="space-y-4">
      <section class="rounded-2xl border border-slate-200 p-4">
        <h3 class="text-sm font-bold text-slate-700">⏱ Lengths</h3>
        <p class="text-xs text-slate-500">A Pomodoro is 25 minutes of focus, a 5-minute break, and a longer break after every 4.</p>
        <div class="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-3">
          ${num('focus', 'Focus', 1, 180, 'min')}${num('short', 'Break', 1, 60, 'min')}${num('long', 'Long break', 1, 90, 'min')}${num('longEvery', 'Long break every', 0, 12, 'sessions (0 = never)')}
        </div>
        <div class="mt-3 grid sm:grid-cols-2 gap-2">
          ${check('autoBreak', 'Start breaks by themselves', 'When a focus session ends, the break starts right away.')}
          ${check('autoFocus', 'Start focus by itself', 'When a break ends, the next focus session starts right away.')}
        </div>
      </section>
      <section class="rounded-2xl border border-slate-200 p-4">
        <div class="flex items-start justify-between gap-3">
          <div><h3 class="text-sm font-bold text-slate-700">🔊 Sounds</h3>
          <p class="text-xs text-slate-500">Played when it’s time to stop and when it’s time to start again. Nestwork has to be open in a tab.</p></div>
          <label class="shrink-0 flex items-center gap-2 text-sm font-semibold"><input type="checkbox" data-ts="sound" ${c.sound ? 'checked' : ''} class="rounded text-lavender-700"> On</label>
        </div>
        <div data-ts-sound-opts class="mt-3 grid sm:grid-cols-2 gap-3 ${c.sound ? '' : 'opacity-50 pointer-events-none'}">
          <div class="sm:col-span-2 flex flex-wrap items-center justify-between gap-2 rounded-xl bg-lavender-50 px-3 py-2.5">
            <p class="text-xs text-slate-600">Pick the sounds and volume (a preset or your own audio file) in <b>Focus session over</b> and <b>Break over</b>.</p>
            <button type="button" data-ts-sounds class="shrink-0 rounded-full border border-lavender-300 bg-white px-3 py-1.5 text-xs font-semibold text-lavender-700 hover:bg-lavender-50">🔊 Open Sounds</button>
          </div>
          <label class="block">
            <span class="block text-xs font-semibold text-slate-600 mb-1">Play it <span class="font-normal text-slate-400">(presets; your files play once)</span></span>
            <select data-ts="repeat" class="w-full rounded-lg border border-slate-300 bg-white py-1.5 pl-2.5 pr-8 text-sm">${[1, 2, 3, 5].map(n => `<option value="${n}" ${Number(c.repeat) === n ? 'selected' : ''}>${n === 1 ? 'Once' : `${n} times`}</option>`).join('')}</select>
          </label>
          <div class="sm:col-span-2">${check('warn', 'One-minute warning', 'A soft tick when one minute is left.')}</div>
        </div>
      </section>
      <section class="rounded-2xl border border-slate-200 p-4">
        <h3 class="text-sm font-bold text-slate-700">🔔 Notifications</h3>
        <div class="mt-2">${check('notify', 'Desktop notification', 'Also show a notification, handy when Nestwork is in a background tab. Your browser asks for permission the first time you start the timer.')}</div>
      </section>
      <div class="flex items-center justify-between gap-2">
        <p data-ts-saved class="text-xs text-slate-400">Changes save by themselves (in this browser).</p>
        <button type="button" data-ts-reset class="rounded-full border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50">Restore defaults</button>
      </div>
    </div>`;
}

function bindTimerSettings(pane) {
  const saved = pane.querySelector('[data-ts-saved]');
  const save = el => {
    const key = el.dataset.ts;
    let value = el.type === 'checkbox' ? el.checked : el.type === 'number' || el.type === 'range' || key === 'repeat' ? Number(el.value) : el.value;
    if (el.type === 'number') {
      value = Math.round(Math.max(Number(el.min), Math.min(Number(el.max), value || Number(el.min))));
      el.value = value;
    }
    saveTimerSettings({ [key]: value });
    if (key === 'sound') pane.querySelector('[data-ts-sound-opts]').classList.toggle('opacity-50', !value), pane.querySelector('[data-ts-sound-opts]').classList.toggle('pointer-events-none', !value);
    if (key === 'notify' && value && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
    saved.textContent = 'Saved ✓';
    animate(saved, 'fade-in');
    if (onHudPage()) renderCurrent();
  };
  pane.querySelectorAll('[data-ts]').forEach(el => {
    el.addEventListener('change', () => save(el));
  });
  pane.querySelector('[data-ts-sounds]').onclick = () => document.querySelector('.st-tab[data-tab="sounds"]')?.click();
  pane.querySelector('[data-ts-reset]').onclick = () => {
    localSet('nestwork.timerSettings', '{}');
    if (!hud.timer.endsAt) { hud.timer.left = modeLength(); saveTimer(); }
    pane.innerHTML = timerSettingsHTML();
    bindTimerSettings(pane);
    if (onHudPage()) renderCurrent();
    toast('Timer settings back to the defaults.', 'success');
  };
}

// ---------------------------------------------------------------- the 1-second tick
function tickHud() {
  const now = new Date();
  const set = (sel, text) => app.querySelectorAll(sel).forEach(el => { if (el.textContent !== text) el.textContent = text; });
  const parts = Object.fromEntries(CLOCK_FMT.formatToParts(now).map(x => [x.type, x.value]));
  const timeEl = app.querySelector('[data-hud-time]'), timeText = `${parts.hour}:${parts.minute}`;
  if (timeEl && timeEl.textContent !== timeText) {   // the colon blinks; the glitch layers carry the real time
    timeEl.innerHTML = `${parts.hour}<span class="colon">:</span>${parts.minute}`;
    timeEl.dataset.text = timeText;
  }
  const sec = app.querySelector('[data-hud-sec]'), secText = `:${pad(now.getSeconds())}`;
  if (sec && sec.textContent !== secText) { sec.textContent = secText; animate(sec, 'tick'); }   // flashes as each second passes
  set('[data-hud-ampm]', parts.dayPeriod || '');
  set('[data-hud-date]', now.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }));
  set('[data-hud-greet]', greeting(now.getHours()));
  set('[data-hud-week]', `Week ${weekNumber(now)} · ${now.toLocaleTimeString([], { timeZoneName: 'short' }).split(' ').pop()}`);
  const mins = now.getHours() * 60 + now.getMinutes(), left = 1440 - mins;
  set('[data-hud-dayleft]', `${Math.floor(left / 60)} h ${pad(left % 60)} min left · ${Math.round(mins / 14.4)}%`);
  app.querySelectorAll('[data-hud-dayfill]').forEach(el => { el.style.width = `${mins / 14.4}%`; });
  // Connection: what the last check against the server actually found.
  set('[data-hud-conn]', conn.ok === false ? 'Server not responding' : `Connected · ${location.host}`);
  app.querySelectorAll('[data-hud-conn-dot]').forEach(el => {
    el.classList.toggle('bg-mint-300', conn.ok !== false); el.classList.toggle('text-mint-300', conn.ok !== false);
    el.classList.toggle('bg-blush-300', conn.ok === false); el.classList.toggle('text-blush-300', conn.ok === false);
  });
  if (conn.at) set('[data-hud-checked]', agoText(conn.at));
  if (hud.usage?.checked_at) set('[data-hud-usage-ago]', agoText(hud.usage.checked_at * 1000));
  if (Date.now() - (hud.usageAt || 0) > 120e3) loadUsage();   // every 2 minutes while the HUD is open
  app.querySelectorAll('[data-hud-agent-led]').forEach(el => {
    const l = agentLed({ claude_found: !el.classList.contains('text-blush-300') }, conn.v?.chat_running);
    if (el.className !== `hud-led ${l.cls}`) { el.className = `hud-led ${l.cls}`; el.title = l.title; }
  });
  app.querySelectorAll('[data-hud-agent-status]').forEach(el => {
    const html = agentStatus(!app.querySelector('[data-hud-agent-led]')?.classList.contains('text-blush-300'), conn.v);
    if (el.innerHTML !== html) el.innerHTML = html;
  });
  app.querySelectorAll('[data-hud-agent]').forEach(el => {
    const html = conn.v?.chat_running ? '<span class="text-peach-700">working…</span>' : 'idle';
    if (el.innerHTML !== html) el.innerHTML = html;
  });
  app.querySelectorAll('[data-hud-rel]').forEach(el => { el.textContent = relTime(new Date(Number(el.dataset.hudRel))); });
  // Timer
  const t = hud.timer, sw = isStopwatch(), ms = sw ? swMs() : timerLeft(), running = sw ? t.swFrom : t.endsAt;
  const tEl = app.querySelector('[data-hud-timer]'), tText = sw ? fmtElapsed(ms) : fmtClock(ms);
  if (tEl && tEl.textContent !== tText) { tEl.textContent = tText; if (running) animate(tEl, 'tick'); }
  const ringEl = app.querySelector('[data-hud-timer-ring]');
  if (ringEl) { const c = parseFloat(ringEl.getAttribute('stroke-dasharray')); ringEl.setAttribute('stroke-dashoffset', String(c * (1 - (sw ? swRing() : ms / modeLength())))); }
  document.title = running && (location.hash === '' || location.hash === '#/') ? `${tText} · Nestwork` : document.title.replace(/^[\d:]+ · /, '');
  if (sw && running && ms >= 1000) app.querySelector('[data-hud-sw-finish]')?.removeAttribute('disabled');
}
setInterval(() => {
  timerTick();
  if (onHudPage()) tickHud();
}, 1000);

// Keep the HUD's chat button in step with the header button and the panel's ✕.
new MutationObserver(() => {
  const open = document.body.classList.contains('chat-open');
  app.querySelectorAll('[data-hud-chat]').forEach(b => {
    if (b.getAttribute('aria-pressed') === String(open)) return;
    b.setAttribute('aria-pressed', open);
    b.className = `relative shrink-0 rounded-full border px-3 py-1.5 text-sm font-bold ${open ? 'bg-lavender-700 border-lavender-700 text-white' : 'bg-white border-lavender-200 text-lavender-700 hover:bg-lavender-50'}`;
    b.innerHTML = `<span class="inline-flex items-center gap-1.5">${CHAT_ICON}${open ? 'Hide chat' : 'Chat'}</span>`;
  });
}).observe(document.body, { attributes: true, attributeFilter: ['class'] });

// ---------------------------------------------------------------- keyboard: V switches view, C chat, F timer, N new to-do, ? shortcuts (settings.js lists them all)
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
  const el = document.activeElement;
  if (el && (el.matches('input, textarea, select') || el.isContentEditable)) return;
  if (modalRoot.querySelector('[data-backdrop]:not(.closing)') || location.hash.startsWith('#/awaken')) return;
  const k = e.key.toLowerCase();
  const onHud = location.hash === '' || location.hash === '#/';
  if (k === 'v') {   // HUD → Focus → Workflow → Agent's Work → HUD
    e.preventDefault();
    location.hash = onHud ? '#/focus' : location.hash.startsWith('#/focus') ? '#/projects'
      : location.hash.startsWith('#/projects') || location.hash.startsWith('#/calendar') || location.hash.startsWith('#/b/') ? '#/work' : '#/';
  }
  else if (k === 'c') { e.preventDefault(); setChatOpen(!document.body.classList.contains('chat-open')); }
  else if (e.key === '?') { e.preventDefault(); settingsModal('shortcuts'); }
  else if (k === 'f' && onHud) { e.preventDefault(); timerToggle(); }
  else if (k === 'n' && /^#\/b\/\d+/.test(location.hash) && state.data) { e.preventDefault(); newTodoModal(); }
});
