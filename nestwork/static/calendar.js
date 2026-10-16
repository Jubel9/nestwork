// The Calendar (#/calendar, Workflow → Calendar): every project's dates on one calendar, day by day.
// Fixed dates come from elsewhere: due dates (all-day, or at their time), the next rounds of repeating to-dos
// (dashed, ahead of time) and reminders. The day's plan is what's put at a time, by you or your agent: to-dos,
// events and breaks. Click an empty time to add one, drag to move it (to another time or day), drag its
// bottom edge to make it longer, drag a deadline to another day to change its due date, and drag a to-do
// from "To schedule" onto a time. Past days keep their history (History on): focus sessions at the time they
// ran, what you did (the activity log your agent keeps from the chat; click one to fix its times), what was
// finished that day, and reminders that went out; click the date to jump anywhere, even a year
// back. Day, Week and Month views; ← → and T to move around. Server side:
// nestwork/calendar_view.py and plan.py (add_item, update_item, remove_item). Uses app.js, hud.js, editor.js.

const CAL_H = 44;              // pixels per hour on the time grid
const CAL_SNAP = 15;           // minutes
const CAL_LENGTHS = [15, 25, 30, 45, 50, 60, 90, 120, 180, 240];
const cal = {
  mode: localGet('nestwork.calMode') || (matchMedia('(max-width: 767px)').matches ? 'day' : 'week'),
  anchor: null,                // the day the view is around (YYYY-MM-DD)
  data: null, month: null,     // the shown range, and the mini month's
  scroll: null,                // the time grid's scroll position, kept across redraws
  drag: null,                  // a drag in progress
  justDragged: 0,              // the click that ends a drag isn't a click
  land: null,                  // the item to animate after the next draw (just moved or added)
  dir: 0,                      // -1 / 1: which way the last ‹ › went, for the slide
  history: localGet('nestwork.calHistory') !== '0',   // show what happened: focus, finished, reminders sent
};

const calAdd = (iso, n) => { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return isoDay(d); };
const calMonday = iso => { const d = new Date(iso + 'T00:00:00'); return calAdd(iso, -((d.getDay() + 6) % 7)); };
const calMin = hm => { const [h, m] = hm.split(':').map(Number); return h * 60 + m; };
const calHM = m => `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`;
const calEnd = (start, minutes) => (calMin(start) + minutes >= 1440 ? '24:00' : calHM(calMin(start) + minutes));
const calToday = () => cal.data?.today || isoDay(new Date());
const calDate = (iso, opts) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, opts);

function calRange(mode = cal.mode, anchor = cal.anchor) {
  if (mode === 'day') return { start: anchor, days: 1 };
  if (mode === 'week') return { start: calMonday(anchor), days: 7 };
  return { start: calMonday(anchor.slice(0, 8) + '01'), days: 42 };
}

function calLabel() {
  const { start, days } = calRange();
  if (cal.mode === 'day') return calDate(start, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  if (cal.mode === 'month') return calDate(cal.anchor, { month: 'long', year: 'numeric' });
  const end = calAdd(start, days - 1), sameMonth = start.slice(0, 7) === end.slice(0, 7);
  return `${calDate(start, sameMonth ? { day: 'numeric' } : { day: 'numeric', month: 'short' })} – ${calDate(end, { day: 'numeric', month: 'short', year: 'numeric' })}`;
}

// ---------------------------------------------------------------- what's on a day
// Every entry becomes one item: {key, kind, date, start (minutes, or null for all day), dur, title, sub, tone, raw}.
function calItems(day) {
  const out = [];
  day.plan.forEach(it => out.push({
    key: `p:${it.id}`, kind: 'plan', date: day.date, start: calMin(it.start), dur: it.minutes, raw: it,
    title: it.title || (it.kind === 'break' ? 'Break' : 'Untitled'), done: it.done, gone: it.gone,
    sub: `${it.start}–${calEnd(it.start, it.minutes)}${it.project ? ` · ${it.project}` : ''}`,
    tone: it.done ? 'done' : it.kind === 'break' ? 'break' : it.kind === 'event' ? 'event' : 'todo',
    agent: it.by && it.by !== 'you' ? it.by : null,
  }));
  day.due.forEach(d => out.push({
    key: `${d.kind === 'repeat' ? 'r' : 'd'}:${d.block_id}:${day.date}`, kind: d.kind, date: day.date, raw: d,
    start: d.time ? calMin(d.time) : null, dur: 30, title: d.title, done: d.done,
    sub: `${d.kind === 'repeat' ? `🔁 ${d.repeat_text.replace(/^Every /, 'every ')}` : d.overdue ? 'Overdue' : 'Due'}${d.time ? ` ${d.time}` : ''} · ${d.project}`,
    tone: d.kind === 'repeat' ? 'repeat' : d.done ? 'done' : d.overdue ? 'overdue' : 'due',
  }));
  out.forEach(it => { if (it.raw?.archived) it.sub += ' · archived'; });
  day.reminders.forEach(r => {
    if (r.sent && !cal.history) return;
    out.push({
      key: `m:${r.id}`, kind: 'reminder', date: day.date, start: calMin(r.time), dur: 20, raw: r,
      title: r.title, sub: `⏰ ${r.time}${r.sent ? ' · went out' : ''}${r.block_title ? ` · ${r.block_title}` : ''}`, tone: r.sent ? 'sent' : 'reminder',
    });
  });
  if (!cal.history) return out;
  (day.focus || []).forEach(f => out.push({
    key: `s:${f.id}`, kind: 'focus', date: day.date, start: calMin(f.start), dur: f.minutes, raw: f,
    title: f.title, sub: `◎ ${f.start}–${calEnd(f.start, f.minutes)} · focused ${fmtMin(f.focus_min)}${f.rating ? ` · ${'★'.repeat(f.rating)}` : ''}`, tone: 'focus',
  }));
  (day.activities || []).forEach(a => out.push({
    key: `a:${a.id}`, kind: 'activity', date: day.date, start: calMin(a.start), dur: a.minutes, raw: a,
    title: a.title, sub: `● ${a.start}–${a.running ? 'now' : a.end.slice(11, 16)}${a.running ? ' · still going' : ` · ${fmtMin(a.real_minutes)}`}${a.block_title ? ` · ${a.block_title}` : ''}`,
    tone: 'activity', agent: a.by && a.by !== 'you' ? a.by : null,
  }));
  if (day.finished?.length) out.push({
    key: `f:${day.date}`, kind: 'finished', date: day.date, start: null, dur: 0, raw: day.finished,
    title: `${day.finished.length} finished`, sub: day.finished.map(c => `${c.time} ${c.title}`).slice(0, 8).join('\n'), tone: 'finished',
  });
  return out;
}

const CAL_TONE = {
  todo: 'bg-lavender-100 border-lavender-300 text-lavender-800',
  event: 'bg-baby-100 border-baby-300 text-baby-700',
  break: 'bg-slate-100 border-slate-300 text-slate-500 italic border-dashed',
  done: 'bg-mint-100 border-mint-300 text-mint-700',
  due: 'bg-butter-100 border-butter-300 text-butter-700',
  overdue: 'bg-blush-100 border-blush-300 text-blush-700',
  repeat: 'bg-lilac-50 border-lilac-300 text-lilac-700 border-dashed',
  reminder: 'bg-peach-100 border-peach-300 text-peach-700',
  sent: 'bg-peach-50 border-peach-200 text-peach-700 opacity-60',
  focus: 'bg-lilac-100/70 border-lilac-300 text-lilac-700 cal-focus',
  finished: 'bg-mint-50 border-mint-300 text-mint-700',
  activity: 'bg-baby-50 border-baby-300 text-baby-700 cal-focus',
};
const CAL_LEGEND = [['todo', 'Planned to-do'], ['event', 'Event'], ['due', 'Due date'], ['repeat', 'Repeats'], ['reminder', 'Reminder'], ['done', 'Done'], ['focus', 'Focused'], ['activity', 'Did (logged)'], ['finished', 'Finished']];
const calMark = it => it.kind === 'due' ? ico('flag') : it.kind === 'reminder' ? ico('bell') : it.kind === 'repeat' ? ico('repeat')
  : it.kind === 'focus' ? ico('timer') : it.kind === 'activity' ? '●' : it.kind === 'finished' ? '✓' : '';
const calAgentMark = it => it.agent ? `<span class="cal-agent" title="Put here by ${esc(it.agent)}">✦</span>` : '';
const calTitle = it => `${it.title}\n${it.sub.replace(/^[🔁⏰◎●] /u, '')}${it.agent ? `\nAdded by ${it.agent}` : ''}${it.kind === 'plan' ? '\nDrag to move · drag the bottom edge to change its length' : it.kind === 'due' ? '\nDrag to another day to change its due date' : ''}`;

// Side by side when they overlap: each item gets a lane and how many lanes its group needs.
function calLanes(items) {
  const sorted = [...items].sort((a, b) => a.start - b.start || b.dur - a.dur);
  let group = [], end = -1;
  const flush = () => {
    const lanes = [];
    group.forEach(it => {
      let i = lanes.findIndex(e => e <= it.start);
      if (i < 0) { i = lanes.length; lanes.push(0); }
      lanes[i] = it.start + Math.max(it.dur, 25);
      it.lane = i;
    });
    group.forEach(it => { it.lanes = lanes.length; });
    group = [];
  };
  sorted.forEach(it => {
    if (group.length && it.start >= end) flush();
    group.push(it);
    end = Math.max(end, it.start + Math.max(it.dur, 25));
  });
  flush();
  return sorted;
}

// Things that start together (within CAL_STACK_MIN of each other: what was planned, what you actually did,
// a reminder at that time…) become one unit drawn as ONE line; other overlaps still sit side by side.
const CAL_STACK_MIN = 20;
function calUnits(items) {
  const units = [];
  [...items].sort((a, b) => a.start - b.start).forEach(it => {
    const u = units.find(x => it.start - x.start < CAL_STACK_MIN && it.start < x.start + x.dur);
    if (u) { u.items.push(it); u.dur = Math.max(u.dur, it.start + it.dur - u.start); }
    else units.push({ start: it.start, dur: it.dur, items: [it] });
  });
  calLanes(units);
  return units;
}

// Things that overlap (a plan, what you actually did, a reminder at that time…) as ONE line instead of a
// pile of slivers. The line spans their whole time; click it to see each of them.
const CAL_STACK_ORDER = ['plan', 'activity', 'focus', 'due', 'repeat', 'reminder'];
function calStackHTML(unit) {
  const group = unit.items, w = 100 / unit.lanes;
  const start = Math.min(...group.map(i => i.start)), end = Math.max(...group.map(i => i.start + Math.max(i.dur, 20)));
  const top = start / 60 * CAL_H, h = Math.max((end - start) / 60 * CAL_H, 20);
  const byTime = [...group].sort((a, b) => a.start - b.start || CAL_STACK_ORDER.indexOf(a.kind) - CAL_STACK_ORDER.indexOf(b.kind));
  const lead = [...group].sort((a, b) => CAL_STACK_ORDER.indexOf(a.kind) - CAL_STACK_ORDER.indexOf(b.kind) || a.start - b.start)[0];
  const rows = Math.max(0, Math.floor((h - 22) / 15));       // room for a line per item when it's tall enough
  const rest = byTime.filter(i => i !== lead);
  const list = rows ? rest.slice(0, rows).map(i => `<p class="cal-stack-row">${calMark(i) || '•'}<span class="truncate ${i.done ? 'line-through opacity-70' : ''}">${esc(i.title)}</span></p>`).join('') : '';
  const title = `${calHM(start)}–${calHM(Math.min(end, 1440))} · ${group.length} things\n${byTime.map(i => `${calHM(i.start)} ${i.title}`).join('\n')}\nClick to see them`;
  return `<div class="cal-ev cal-stack is-fixed ${CAL_TONE[lead.tone]}" data-cal-stack="${esc(byTime.map(i => i.key).join('|'))}" data-cal-stack-day="${lead.date}"
      style="top:${top}px;height:${h}px;left:calc(${unit.lane * w}% + 2px);width:calc(${w}% - 4px)" title="${esc(title)}">
    <p class="cal-ev-title">${calMark(lead)}<span class="min-w-0 flex-1 truncate">${esc(lead.title)}</span><span class="cal-stack-n">+${group.length - 1}</span></p>
    ${rows && rows < rest.length ? list + `<p class="cal-stack-row opacity-70">+${rest.length - rows} more</p>` : list}
  </div>`;
}

// Everything in one stacked line: what was planned, what you did, reminders… Click one to open it as usual.
function calStackOpen(keys, date) {
  const items = keys.map(calFind).filter(Boolean);
  if (!items.length) return;
  const start = Math.min(...items.map(i => i.start)), end = Math.max(...items.map(i => i.start + i.dur));
  const KIND = { plan: 'Planned', activity: 'Did', focus: 'Focused', due: 'Due', repeat: 'Repeats', reminder: 'Reminder' };
  openModal(`
    <p class="hud-label mb-2">${ico('calendar')}At this time</p>
    <h2 class="text-lg font-bold text-slate-800">${esc(fmtDay(date))} · ${calHM(start)}–${calHM(Math.min(end, 1440))}</h2>
    <ul class="mt-4 space-y-1.5 max-h-[60vh] overflow-y-auto scroll-thin -mx-1 px-1">
      ${items.map((it, n) => `
        <li data-cs="${n}" class="flex items-start gap-2.5 rounded-xl border-l-2 px-3 py-2 text-sm cursor-pointer hover:brightness-105 ${CAL_TONE[it.tone]}">
          <span class="hud-num text-xs w-10 shrink-0 pt-0.5">${calHM(it.start)}</span>
          <span class="min-w-0 flex-1">
            <span class="block font-semibold ${it.done || it.gone ? 'line-through opacity-70' : ''}">${calAgentMark(it)}${calMark(it)} ${esc(it.title)}</span>
            <span class="block text-xs opacity-80">${esc(KIND[it.kind] || '')}${KIND[it.kind] ? ' · ' : ''}${esc(it.sub.replace(/^[🔁⏰◎●] /u, ''))}</span>
          </span>
          <span class="shrink-0 text-xs opacity-60 pt-0.5">${it.kind === 'plan' ? 'Edit' : 'Open'} ›</span>
        </li>`).join('')}
    </ul>
    <div class="flex justify-end mt-5"><button data-close class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">Close</button></div>`, root => {
    root.querySelectorAll('[data-cs]').forEach(li => li.onclick = () => { closeModal(); calOpen(items[Number(li.dataset.cs)]); });
    stagger(root.querySelectorAll('li'), 'pop-in', 25, 300);
  });
}

// ---------------------------------------------------------------- drawing
function calEventHTML(it) {
  const top = it.start / 60 * CAL_H, h = Math.max(it.dur / 60 * CAL_H, 20);
  const w = 100 / it.lanes, tight = h < 36;
  const drag = it.kind === 'plan' || (it.kind === 'due' && !it.done);
  return `<div class="cal-ev ${CAL_TONE[it.tone]} ${drag ? '' : 'is-fixed'} ${it.kind !== 'plan' ? 'is-mark' : ''}" data-cal-item="${esc(it.key)}" ${drag ? 'data-cal-drag' : ''}
      style="top:${top}px;height:${h}px;left:calc(${it.lane * w}% + 2px);width:calc(${w}% - 4px)" title="${esc(calTitle(it))}">
    <p class="cal-ev-title ${it.done || it.gone ? 'line-through opacity-70' : ''}">${calAgentMark(it)}${calMark(it)}${esc(it.title)}${tight ? ` <span class="cal-ev-time">${esc(it.sub.split(' · ')[0].replace(/^[🔁⏰◎●] /u, ''))}</span>` : ''}</p>
    ${tight ? '' : `<p class="cal-ev-sub">${esc(it.sub.replace(/^[🔁⏰◎●] /u, ''))}</p>`}
    ${it.kind === 'plan' ? '<div class="cal-resize" data-cal-resize></div>' : ''}
  </div>`;
}

function calChipHTML(it, withTime = false) {
  const drag = it.kind === 'plan' || (it.kind === 'due' && !it.done);
  const time = withTime && it.start != null ? `<span class="cal-ev-time">${calHM(it.start)}</span>` : '';
  return `<div class="cal-chip ${CAL_TONE[it.tone]} ${drag ? '' : 'is-fixed'}" data-cal-item="${esc(it.key)}" ${drag ? 'data-cal-drag' : ''} title="${esc(calTitle(it))}">
    ${time}<span class="truncate ${it.done ? 'line-through opacity-70' : ''}">${calAgentMark(it)}${calMark(it)}${esc(it.title)}</span></div>`;
}

function calGridHTML(days) {
  const today = calToday(), cols = `3.25rem repeat(${days.length}, minmax(0, 1fr))`;
  const now = new Date(), nowMin = now.getHours() * 60 + now.getMinutes();
  const items = days.map(d => calItems(d));
  const head = days.map(d => {
    const isToday = d.date === today, wd = calDate(d.date, { weekday: days.length === 1 ? 'long' : 'short' });
    return `<button type="button" data-cal-goto="${d.date}" class="cal-dayhead group flex flex-col items-center py-1.5 ${days.length > 1 ? 'hover:bg-lavender-50' : 'cursor-default'}" title="${days.length > 1 ? 'Open this day' : ''}">
      <span class="hud-label !tracking-[.14em] ${isToday ? '!text-lavender-800' : '!text-slate-500'}">${esc(wd)}</span>
      <span class="hud-num mt-0.5 w-8 h-8 flex items-center justify-center rounded-full text-base ${isToday ? 'bg-lavender-700 text-white cal-today-dot' : 'text-slate-700 group-hover:text-lavender-700'}">${Number(d.date.slice(8))}</span>
    </button>`;
  }).join('');
  const allDay = days.map((d, i) => {
    const chips = items[i].filter(it => it.start == null);
    return `<div class="cal-allday min-w-0 border-l border-lavender-100 p-1 space-y-1" data-cal-drop-day="${d.date}" data-cal-allday>${chips.map(c => calChipHTML(c)).join('')}</div>`;
  }).join('');
  const hours = Array.from({ length: 24 }, (_, h) => `<span class="cal-hour hud-num" style="top:${h * CAL_H}px">${h ? `${pad(h)}:00` : ''}</span>`).join('');
  const columns = days.map((d, i) => {
    const units = calUnits(items[i].filter(it => it.start != null));
    const isToday = d.date === today;
    return `<div class="cal-col relative border-l border-lavender-100 ${isToday ? 'is-today' : ''}" data-cal-col="${d.date}">
      ${units.map(u => u.items.length > 1 ? calStackHTML(u) : calEventHTML({ ...u.items[0], lane: u.lane, lanes: u.lanes })).join('')}
      ${isToday ? `<div class="cal-now" data-cal-now style="top:${nowMin / 60 * CAL_H}px" title="Now"></div>` : ''}
    </div>`;
  }).join('');
  return `
    <div class="shrink-0 grid border-b border-lavender-100" style="grid-template-columns:${cols}">
      <div></div>${head}
    </div>
    <div class="shrink-0 grid border-b border-lavender-200 bg-lavender-50/30 max-h-28 overflow-y-auto scroll-thin" style="grid-template-columns:${cols}">
      <div class="hud-label !text-[9px] !tracking-[.12em] !text-slate-400 justify-end pr-2 pt-2">All day</div>${allDay}
    </div>
    <div class="relative flex-1 min-h-0 overflow-y-auto overflow-x-hidden scroll-thin" data-cal-scroll>
      <div class="grid relative" style="grid-template-columns:${cols};height:${24 * CAL_H}px;--cal-h:${CAL_H}px">
        <div class="relative">${hours}</div>${columns}
      </div>
    </div>`;
}

function calMonthHTML(days) {
  const today = calToday(), month = cal.anchor.slice(0, 7);
  const names = days.slice(0, 7).map(d => `<div class="hud-label !tracking-[.14em] !text-slate-500 justify-center py-2">${esc(calDate(d.date, { weekday: 'short' }))}</div>`).join('');
  const cells = days.map(d => {
    const items = calItems(d).sort((a, b) => (a.start ?? -1) - (b.start ?? -1));
    const shown = items.slice(0, 4), more = items.length - shown.length;
    const isToday = d.date === today, out = d.date.slice(0, 7) !== month;
    return `<div class="cal-cell min-w-0 min-h-[5.5rem] border-t border-l border-lavender-100 p-1 flex flex-col gap-0.5 ${out ? 'opacity-45' : ''} ${isToday ? 'is-today' : ''}" data-cal-drop-day="${d.date}" data-cal-cell="${d.date}">
      <button type="button" data-cal-goto="${d.date}" class="self-start hud-num text-xs w-6 h-6 rounded-full flex items-center justify-center ${isToday ? 'bg-lavender-700 text-white cal-today-dot' : 'text-slate-600 hover:bg-lavender-100'}" title="Open this day">${Number(d.date.slice(8))}</button>
      ${shown.map(c => calChipHTML(c, true)).join('')}
      ${more > 0 ? `<button type="button" data-cal-goto="${d.date}" class="self-start text-[11px] font-semibold text-lavender-700 hover:underline px-1">+${more} more</button>` : ''}
    </div>`;
  }).join('');
  return `<div class="shrink-0 grid grid-cols-7">${names}</div>
    <div class="flex-1 min-h-0 grid grid-cols-7 auto-rows-fr overflow-y-auto scroll-thin border-b border-r border-lavender-100">${cells}</div>`;
}

function calMiniHTML() {
  const month = cal.anchor.slice(0, 7), today = calToday(), { start } = calRange();
  const shownEnd = calAdd(start, calRange().days - 1);
  const busy = new Map((cal.month?.days || []).map(d => [d.date, d.plan.length + d.due.length + d.reminders.length + (cal.history ? (d.focus?.length || 0) + (d.activities?.length || 0) + (d.finished?.length || 0) : 0)]));
  const first = calMonday(month + '-01');
  const cells = Array.from({ length: 42 }, (_, i) => {
    const d = calAdd(first, i), n = busy.get(d) || 0, inShown = d >= start && d <= shownEnd;
    return `<button type="button" data-cal-mini="${d}" class="relative hud-num text-[11px] h-7 rounded ${d.slice(0, 7) !== month ? 'text-slate-300' : 'text-slate-600'} ${d === today ? 'bg-lavender-700 !text-white' : inShown ? 'bg-lavender-100 text-lavender-800' : 'hover:bg-lavender-50'}" title="${esc(calDate(d, { weekday: 'long', day: 'numeric', month: 'long' }))}${n ? ` · ${n} thing${n > 1 ? 's' : ''}` : ''}">
      ${Number(d.slice(8))}${n ? `<span class="absolute bottom-0.5 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full ${d === today ? 'bg-white' : 'bg-lilac-300'}"></span>` : ''}</button>`;
  }).join('');
  const names = ['M', 'T', 'W', 'T', 'F', 'S', 'S'].map(n => `<span class="text-center text-[10px] font-mono text-slate-400">${n}</span>`).join('');
  return `<section class="cal-card shrink-0 rounded-3xl bg-white border border-lavender-200 shadow-sm p-3">
    <div class="flex items-center justify-between mb-1.5">
      <button type="button" data-cal-mini-step="-1" class="w-7 h-7 rounded-full text-slate-500 hover:bg-lavender-50 hover:text-lavender-700" aria-label="Previous month">‹</button>
      <p class="hud-label">${esc(calDate(month + '-01', { month: 'long', year: 'numeric' }))}</p>
      <button type="button" data-cal-mini-step="1" class="w-7 h-7 rounded-full text-slate-500 hover:bg-lavender-50 hover:text-lavender-700" aria-label="Next month">›</button>
    </div>
    <div class="grid grid-cols-7 gap-0.5">${names}${cells}</div>
  </section>`;
}

function calCandidatesHTML() {
  const cands = cal.data.candidates;
  const on = new Map();                         // to-do id -> when it's already on the shown days
  cal.data.days.forEach(d => d.plan.forEach(it => { if (it.block_id && !on.has(it.block_id)) on.set(it.block_id, `${calDate(d.date, { weekday: 'short' })} ${it.start}`); }));
  return `<section class="cal-card flex-1 min-h-0 flex flex-col rounded-3xl bg-white border border-lavender-200 shadow-sm p-3">
    <div class="shrink-0 flex items-center justify-between gap-2 mb-1">
      <p class="hud-label">To schedule<span class="hud-count">· ${cands.length}</span></p>
    </div>
    <p class="shrink-0 text-xs text-slate-400 mb-2">Drag a to-do onto a time, or onto “All day” to give it a due date. + puts it in the next free slot.</p>
    <ul class="flex-1 min-h-0 overflow-y-auto scroll-thin space-y-1.5 -mx-1 px-1">
      ${cands.length ? cands.map(c => `
        <li class="cal-cand group flex items-start gap-2 rounded-xl border border-lavender-100 bg-white px-2.5 py-2 cursor-grab hover:border-lavender-300 hover:bg-lavender-50/50 ${on.has(c.id) ? 'opacity-60 hover:opacity-100' : ''}" data-cal-cand="${c.id}" data-cal-drag title="${esc(c.title)} · drag onto the calendar">
          <span class="mt-1.5 w-2 h-2 shrink-0 rounded-full ${STAGE_STYLE[c.status].dot}"></span>
          <span class="min-w-0 flex-1">
            <span class="block truncate text-sm font-semibold text-slate-800">${esc(c.title)}</span>
            <span class="block truncate text-[11px] text-slate-400">${esc(c.project)} · ${fmtMin(c.suggested_min)}${c.due_date ? ` · due ${esc(calDate(c.due_date, { month: 'short', day: 'numeric' }))}` : ''}${c.priority === 3 ? ' · high' : ''}</span>
            ${on.has(c.id) ? `<span class="block text-[11px] font-semibold text-lavender-700">${ico('calendar')} On ${esc(on.get(c.id))}</span>` : ''}
          </span>
          <button type="button" data-cal-cand-add="${c.id}" class="shrink-0 w-6 h-6 rounded-full border border-lavender-200 text-lavender-700 font-bold hover:bg-lavender-100" title="Put it in the next free slot on ${esc(calDate(cal.anchor, { weekday: 'short', day: 'numeric', month: 'short' }))}">+</button>
        </li>`).join('') : '<li class="text-sm text-slate-400 py-4 text-center">Nothing ready or due. Nice.</li>'}
    </ul>
    <div class="shrink-0 mt-2 pt-2 border-t border-lavender-100 flex flex-wrap gap-x-3 gap-y-1">
      ${CAL_LEGEND.map(([t, l]) => `<span class="inline-flex items-center gap-1 text-[11px] text-slate-500"><span class="w-2.5 h-2.5 rounded-sm border-l-2 ${CAL_TONE[t]}"></span>${l}</span>`).join('')}
    </div>
  </section>`;
}

// ---------------------------------------------------------------- page
async function renderCalendar() {
  if (cal.drag) return;                                 // redraw after the drop instead
  state.data = null;
  cal.anchor ??= isoDay(new Date());
  const { start, days } = calRange();
  const mini = calRange('month');
  const [data, month] = await Promise.all([
    api('GET', `/api/calendar?start=${start}&days=${days}`),
    cal.mode === 'month' ? null : api('GET', `/api/calendar?start=${mini.start}&days=42`).catch(() => null),
  ]);
  cal.data = data; cal.month = month || data;
  document.title = 'Calendar · Nestwork';
  const oldScroll = app.querySelector('[data-cal-scroll]');
  if (oldScroll && motion.page === 'calendar') cal.scroll = oldScroll.scrollTop;
  const modes = [['day', 'Day'], ['week', 'Week'], ['month', 'Month']];
  app.innerHTML = `
    <div class="one-screen-fill flex flex-col">
      <div class="shrink-0 flex flex-wrap items-end justify-between gap-3 mb-3">
        ${workflowHeadHTML('calendar', 'Every project’s dates in one place. Click an empty time to add something; drag to move it or stretch it.')}
        <div class="flex flex-wrap items-center gap-2">
          <button data-cal-today class="rounded-full border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50" title="Today (T)">Today</button>
          <span class="inline-flex rounded-full border border-lavender-200 bg-white/60">
            <button data-cal-step="-1" class="w-8 h-8 rounded-full text-slate-600 hover:bg-lavender-50 hover:text-lavender-700" aria-label="Previous" title="Previous (←)">‹</button>
            <button data-cal-step="1" class="w-8 h-8 rounded-full text-slate-600 hover:bg-lavender-50 hover:text-lavender-700" aria-label="Next" title="Next (→)">›</button>
          </span>
          <button type="button" data-cal-jump class="font-display font-bold text-sm sm:text-base text-slate-800 min-w-[9rem] text-left rounded-lg px-1.5 -mx-1.5 hover:bg-lavender-50 hover:text-lavender-700" title="Go to a date (G)">${esc(calLabel())}</button>
          <span class="inline-flex rounded-full border border-lavender-200 bg-white/60 p-0.5 text-xs font-semibold font-display uppercase tracking-wide">
            ${modes.map(([k, l]) => `<button data-cal-mode="${k}" class="rounded-full px-2.5 py-1 ${cal.mode === k ? 'bg-lavender-700 text-white' : 'text-slate-500 hover:text-lavender-700'}" title="${l} (${l[0]})">${l}</button>`).join('')}
          </span>
          <button data-cal-history aria-pressed="${cal.history}" class="inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm ${cal.history ? 'border-lilac-300 bg-lilac-50 text-lilac-700' : 'border-slate-300 text-slate-500 hover:bg-slate-50'}" title="Show what happened: focus sessions, what you did (logged by your agent), what you finished and reminders that went out (H)">${ico('timer')}History</button>
          <button data-cal-add class="rounded-full border border-lavender-300 px-3 py-1.5 text-sm text-lavender-700 hover:bg-lavender-50" title="Add something (N)">+ Add</button>
          <button data-cal-agent class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-1.5 shadow-sm" title="Your agent looks at these days and fills the open time with you">✦ Plan with my agent</button>
        </div>
      </div>
      <div class="flex-1 min-h-0 grid gap-4 grid-cols-[minmax(0,1fr)] lg:grid-cols-[minmax(0,1fr)_17rem]">
        <section class="cal-card cal-main relative flex flex-col min-h-0 h-[75vh] lg:h-auto rounded-3xl bg-white border border-lavender-200 shadow-sm overflow-hidden" data-cal-main>
          ${cal.mode === 'month' ? calMonthHTML(data.days) : calGridHTML(data.days)}
        </section>
        <aside class="flex flex-col gap-4 min-h-0">${calMiniHTML()}${calCandidatesHTML()}</aside>
      </div>
    </div>`;
  const entering = enterPage('calendar', 0);
  const scroller = app.querySelector('[data-cal-scroll]');
  if (scroller) {
    const inRange = data.days.some(d => d.date === data.today), now = new Date();
    const top = cal.scroll ?? Math.max(0, ((inRange ? now.getHours() + now.getMinutes() / 60 : 8) - 1.5) * CAL_H);
    // Tailwind writes the panel's height a moment after the HTML lands: scroll once it has one.
    scroller.scrollTop = top;
    requestAnimationFrame(() => requestAnimationFrame(() => { if (scroller.isConnected) scroller.scrollTop = top; cal.scroll = top; }));
    scroller.addEventListener('scroll', () => { cal.scroll = scroller.scrollTop; }, { passive: true });
  }
  if (entering) {
    stagger(app.querySelectorAll('.cal-card'), 'pop-in', 70);
    setTimeout(() => stagger(app.querySelectorAll('.cal-ev, .cal-chip'), 'pop-in', 14, 300), 260);
    animate(app.querySelector('[data-wf-switch="calendar"]'), 'tab-on');
  }
  if (cal.land) {
    const el = app.querySelector(`[data-cal-item="${CSS.escape(cal.land)}"]`);
    if (el) { el.scrollIntoView({ block: 'nearest' }); animate(el, 'land'); }
    cal.land = null;
  }
  bindCalendar();
}

// Switching Day / Week / Month or going ‹ › : the old view leaves, the new one comes in from that side.
async function calGo(change, how = 'view') {
  const main = app.querySelector('[data-cal-main]');
  Object.assign(cal, change);
  if (change.mode) localSet('nestwork.calMode', cal.mode);
  if (change.mode) cal.scroll = null;
  if (main) await animateOut([main.children[main.children.length - 1]], 'view-out', 120);
  await renderCalendar();
  const fresh = app.querySelector('[data-cal-main]');
  if (!fresh) return;
  if (how === 'view') { animate(fresh, 'view-in'); animate(app.querySelector(`[data-cal-mode="${cal.mode}"]`), 'tab-on'); }
  else animate(fresh, cal.dir < 0 ? 'lb-prev' : 'lb-next');
  stagger(fresh.querySelectorAll('.cal-ev, .cal-chip'), 'pop-in', 10, 200);
}

function calStep(n) {
  cal.dir = n;
  const a = cal.anchor;
  const next = cal.mode === 'day' ? calAdd(a, n) : cal.mode === 'week' ? calAdd(a, 7 * n)
    : (() => { const d = new Date(a.slice(0, 8) + '01T00:00:00'); d.setMonth(d.getMonth() + n); return isoDay(d); })();
  calGo({ anchor: next }, 'step');
}

function calFind(key) {
  for (const d of cal.data.days) { const it = calItems(d).find(i => i.key === key); if (it) return it; }
  return null;
}

function bindCalendar() {
  const $ = s => app.querySelector(s);
  $('[data-cal-today]').onclick = () => { cal.dir = calToday() < cal.anchor ? -1 : 1; cal.scroll = null; calGo({ anchor: calToday() }, 'step'); };
  app.querySelectorAll('[data-cal-step]').forEach(b => b.onclick = () => calStep(Number(b.dataset.calStep)));
  app.querySelectorAll('[data-cal-mode]').forEach(b => b.onclick = () => { if (b.dataset.calMode !== cal.mode) calGo({ mode: b.dataset.calMode }); });
  app.querySelectorAll('[data-cal-goto]').forEach(b => b.onclick = e => {
    e.stopPropagation();
    if (cal.mode === 'day') return;
    calGo({ mode: 'day', anchor: b.dataset.calGoto });
  });
  app.querySelectorAll('[data-cal-mini]').forEach(b => b.onclick = () => {
    const d = b.dataset.calMini;
    cal.dir = d < cal.anchor ? -1 : 1;
    calGo({ anchor: d }, 'step');
  });
  app.querySelectorAll('[data-cal-mini-step]').forEach(b => b.onclick = () => {
    const d = new Date(cal.anchor.slice(0, 8) + '01T00:00:00'); d.setMonth(d.getMonth() + Number(b.dataset.calMiniStep));
    cal.dir = Number(b.dataset.calMiniStep);
    calGo({ anchor: isoDay(d) }, 'step');
  });
  $('[data-cal-history]').onclick = () => {
    cal.history = !cal.history; localSet('nestwork.calHistory', cal.history ? '1' : '0');
    renderCalendar().then(() => { animate(app.querySelector('[data-cal-history]'), 'tab-on'); stagger(app.querySelectorAll('.cal-focus, [data-cal-item^="f:"]'), 'pop-in', 15, 200); });
  };
  $('[data-cal-jump]').onclick = calJump;
  $('[data-cal-add]').onclick = () => calEditor({ day: cal.anchor, start: calNextSlot(cal.anchor, 30) });
  $('[data-cal-agent]').onclick = () => {
    const { start, days } = calRange();
    setChatOpen(true, { focus: false });
    sendMessage(`Let's plan my calendar for ${calLabel()}: look at it with the calendar tool (start ${start}, ${Math.min(days, 31)} days). Work around what's fixed (due dates, reminders, what's already planned), suggest what to put in the open time with times and short breaks, and add what I agree to with schedule.`);
  };
  app.querySelectorAll('[data-cal-cand-add]').forEach(b => b.onclick = e => {
    e.stopPropagation();
    const c = cal.data.candidates.find(x => x.id === Number(b.dataset.calCandAdd));
    calAddItem({ day: cal.anchor, start: calNextSlot(cal.anchor, c.suggested_min), minutes: c.suggested_min, block_id: c.id });
  });
  // Items: click to open (unless that click ended a drag).
  app.querySelectorAll('[data-cal-item]').forEach(el => el.addEventListener('click', e => {
    e.stopPropagation();
    if (Date.now() - cal.justDragged < 250) return;
    calOpen(calFind(el.dataset.calItem));
  }));
  app.querySelectorAll('[data-cal-stack]').forEach(el => el.addEventListener('click', e => {
    e.stopPropagation();
    if (Date.now() - cal.justDragged < 250) return;
    calStackOpen(el.dataset.calStack.split('|'), el.dataset.calStackDay);
  }));
  // An empty time: add something there. An empty month cell: add on that day.
  app.querySelectorAll('[data-cal-col]').forEach(col => col.addEventListener('click', e => {
    if (e.target !== col || Date.now() - cal.justDragged < 250) return;
    const y = e.clientY - col.getBoundingClientRect().top;
    const m = Math.min(1440 - CAL_SNAP, Math.floor(y / CAL_H * 60 / CAL_SNAP) * CAL_SNAP);
    calEditor({ day: col.dataset.calCol, start: calHM(m) });
  }));
  app.querySelectorAll('[data-cal-cell]').forEach(cell => cell.addEventListener('dblclick', e => {
    if (e.target.closest('[data-cal-item], button')) return;
    calEditor({ day: cell.dataset.calCell, start: calNextSlot(cell.dataset.calCell, 30) });
  }));
  app.querySelectorAll('[data-cal-drag]').forEach(el => el.addEventListener('pointerdown', calPointerDown));
}

// The first free time on a day that fits `minutes`: from now (today) or 09:00, on the quarter hour.
function calNextSlot(day, minutes = 30) {
  const d = cal.data?.days.find(x => x.date === day);
  const busy = d ? calItems(d).filter(i => i.start != null && i.kind === 'plan').map(i => [i.start, i.start + i.dur]) : [];
  const now = new Date();
  let t = day === calToday() ? Math.ceil((now.getHours() * 60 + now.getMinutes()) / CAL_SNAP) * CAL_SNAP : 9 * 60;
  for (let guard = 0; guard < 96 && t + minutes <= 1440; guard++) {
    const clash = busy.find(([a, b]) => t < b && a < t + minutes);
    if (!clash) return calHM(t);
    t = Math.ceil(clash[1] / CAL_SNAP) * CAL_SNAP;
  }
  return calHM(Math.min(t, 1440 - CAL_SNAP));
}

// ---------------------------------------------------------------- changes
async function calAddItem(body) {
  const r = await attempt(() => api('POST', '/api/calendar/items', body));
  if (!r) return null;
  cal.land = `p:${r.item.id}`;
  toast(`Added “${r.item.title}” on ${fmtDay(body.day)} at ${r.item.start}. ${UNDO_KEY} to undo.`, 'success');
  await renderCalendar();
  return r.item;
}

async function calMovePlan(it, changes) {
  const r = await attempt(() => api('PATCH', `/api/calendar/items/${it.date}/${encodeURIComponent(it.raw.id)}`, changes));
  if (!r) return renderCalendar();
  cal.land = `p:${r.item.id}`;
  const day = changes.day || it.date;
  if (changes.day || changes.start) toast(`Moved “${r.item.title}” to ${fmtDay(day)}, ${r.item.start}–${calEnd(r.item.start, r.item.minutes)}.`, 'success', { label: 'Undo', run: undoLast });
  await renderCalendar();
}

async function calMoveDue(blockId, title, day, time) {
  const body = { due_date: day };
  if (time === null) body.clear_due_time = true; else if (time) body.due_time = time;
  const r = await attempt(() => api('PATCH', `/api/blocks/${blockId}`, body));
  if (!r) return renderCalendar();
  cal.land = `d:${blockId}:${day}`;
  toast(`“${title}” is now due ${fmtDay(day)}${time ? ` at ${time}` : ''}.`, 'success', { label: 'Undo', run: undoLast });
  await renderCalendar();
}

function calOpen(it) {
  if (!it) return;
  if (it.kind === 'focus' || it.kind === 'finished') return calHistoryDay(it.date);
  if (it.kind === 'plan') return calEditor({ item: it });
  if (it.kind === 'reminder') return calReminder(it);
  if (it.kind === 'activity') return calActivity(it);
  openBlockPopup(it.raw.block_id);
}

// One entry of the activity log (what you told your agent you did): see it, fix its times, finish or delete it.
function calActivity(it) {
  const a = it.raw;
  const field = 'w-full rounded-lg border border-slate-300 px-2 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300';
  const now = new Date(), pad = n => String(n).padStart(2, '0');
  const nowHM = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
  openModal(`
    <p class="hud-label mb-2">● What you did${a.by && a.by !== 'you' ? ` <span class="normal-case tracking-normal font-normal text-slate-400">· logged by ${esc(a.by)} from the chat</span>` : ''}</p>
    <input id="ca-title" maxlength="200" value="${esc(a.title)}" class="w-full text-lg font-bold text-slate-800 rounded-lg px-1 -mx-1 hover:bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-lavender-300" aria-label="What">
    <p class="text-sm text-slate-500 mt-1">${esc(fmtDay(it.date))}${a.running ? ' · still going' : ` · ${fmtMin(a.real_minutes)}`}${a.block_title ? ` · for “${esc(a.block_title)}”` : ''}</p>
    <div class="grid grid-cols-2 gap-3 mt-4">
      <label class="text-xs font-semibold text-slate-500">Started
        <input id="ca-start" type="datetime-local" value="${esc(a.started_at.slice(0, 16))}" class="${field} mt-1"></label>
      <label class="text-xs font-semibold text-slate-500">Ended ${a.running ? '<span class="font-normal text-slate-400">(empty = still going)</span>' : ''}
        <input id="ca-end" type="datetime-local" value="${esc((a.end || '').slice(0, 16))}" class="${field} mt-1"></label>
    </div>
    <label class="block text-xs font-semibold text-slate-500 mt-3">Note
      <textarea id="ca-note" rows="2" maxlength="2000" class="${field} mt-1 resize-none">${esc(a.note || '')}</textarea></label>
    <div class="flex flex-wrap justify-end gap-2 mt-6">
      ${a.block_id ? '<button data-open class="rounded-lg px-4 py-2 text-lavender-700 hover:bg-lavender-50">Open the to-do</button>' : ''}
      ${a.running ? '<button data-finish class="rounded-lg px-4 py-2 text-mint-700 hover:bg-mint-50">Finished now</button>' : ''}
      <button data-del class="rounded-lg px-4 py-2 text-blush-700 hover:bg-blush-50">Delete</button>
      <button data-save class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">Save</button>
    </div>`, root => {
    const $ = q => root.querySelector(q);
    const save = async body => {
      if (await attempt(() => api('PATCH', `/api/activities/${a.id}`, body))) { closeModal(); renderCalendar(); }
    };
    $('[data-save]').onclick = () => save({
      title: $('#ca-title').value.trim() || a.title, start: $('#ca-start').value.replace('T', ' '),
      end: $('#ca-end').value.replace('T', ' '), note: $('#ca-note').value.trim(),
    });
    $('[data-finish]')?.addEventListener('click', () => save({ end: `${calToday()} ${nowHM}` }));
    $('[data-open]')?.addEventListener('click', () => { closeModal(); openBlockPopup(a.block_id); });
    $('[data-del]').onclick = async () => {
      if (await attempt(() => api('DELETE', `/api/activities/${a.id}`))) { closeModal(); toast(`Deleted “${a.title}” from what you did. ${UNDO_KEY} brings it back.`, 'success'); renderCalendar(); }
    };
  });
}

function calReminder(it) {
  const r = it.raw;
  openModal(`
    <p class="hud-label mb-2">${ico('bell')}Reminder</p>
    <h2 class="text-lg font-bold text-slate-800">${esc(r.title)}</h2>
    <p class="text-sm text-slate-500 mt-1">${r.sent ? 'Went out on ' : ''}${esc(fmtDay(it.date))} at ${esc(r.time)}${r.block_title ? ` · about “${esc(r.block_title)}”` : ''}${r.by && r.by !== 'you' ? ` · set by ${esc(r.by)}` : ''}</p>
    <div class="flex flex-wrap justify-end gap-2 mt-6">
      ${r.block_id ? '<button data-open class="rounded-lg px-4 py-2 text-lavender-700 hover:bg-lavender-50">Open the to-do</button>' : ''}
      ${r.sent ? '' : '<button data-del class="rounded-lg px-4 py-2 text-blush-700 hover:bg-blush-50">Delete reminder</button>'}
      <button data-close class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">Close</button>
    </div>`, root => {
    root.querySelector('[data-open]')?.addEventListener('click', () => { closeModal(); openBlockPopup(r.block_id); });
    root.querySelector('[data-del]')?.addEventListener('click', async () => {
      if (await attempt(() => api('DELETE', `/api/reminders/${r.id}`))) { closeModal(); toast('Reminder deleted.', 'success'); renderCalendar(); }
    });
  });
}

// Jump to any date: a past week to see what you did, or a deadline months ahead.
function calJump() {
  openModal(`
    <p class="hud-label mb-2">${ico('calendar')}Go to a date</p>
    <input id="cj-day" type="date" value="${esc(cal.anchor)}" class="w-full rounded-lg border border-slate-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300">
    <div class="flex flex-wrap gap-2 mt-3">
      ${[['-365', 'A year ago'], ['-30', 'A month ago'], ['-7', 'Last week'], ['0', 'Today'], ['7', 'Next week'], ['30', 'In a month']].map(([n, l]) =>
        `<button type="button" data-cj="${n}" class="rounded-full border border-lavender-200 px-3 py-1 text-xs font-semibold text-lavender-700 hover:bg-lavender-50">${l}</button>`).join('')}
    </div>
    <div class="flex justify-end gap-2 mt-6">
      <button data-close class="rounded-lg px-4 py-2 text-slate-600 hover:bg-slate-100">Cancel</button>
      <button data-go class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">Go</button>
    </div>`, root => {
    const input = root.querySelector('#cj-day');
    const go = d => { if (!d) return; closeModal(); cal.dir = d < cal.anchor ? -1 : 1; cal.scroll = null; calGo({ anchor: d }, 'step'); };
    root.querySelectorAll('[data-cj]').forEach(b => b.onclick = () => go(calAdd(calToday(), Number(b.dataset.cj))));
    root.querySelector('[data-go]').onclick = () => go(input.value);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') go(input.value); });
    input.focus();
  });
}

// What happened on a day: focus sessions, to-dos finished and reminders, in time order.
function calHistoryDay(date) {
  const d = cal.data.days.find(x => x.date === date);
  if (!d) return;
  const rows = [
    ...(d.focus || []).map(f => [f.start, 'focus', `<b>${esc(f.title)}</b> <span class="text-slate-400">· focused ${fmtMin(f.focus_min)}${f.project && f.project !== f.title ? ` · ${esc(f.project)}` : ''}${f.rating ? ` · ${'★'.repeat(f.rating)}` : ''}</span>${f.note ? `<span class="block text-xs text-slate-500 italic">${esc(f.note)}</span>` : ''}`, f.block_id]),
    ...(d.activities || []).map(a => [a.start, 'activity', `Did <b>${esc(a.title)}</b> <span class="text-slate-400">· ${a.running ? 'still going' : `until ${esc(a.end.slice(11, 16))}, ${fmtMin(a.real_minutes)}`}${a.block_title ? ` · ${esc(a.block_title)}` : ''}</span>${a.note ? `<span class="block text-xs text-slate-500 italic">${esc(a.note)}</span>` : ''}`, a.block_id]),
    ...(d.finished || []).map(c => [c.time, 'finished', `Finished <b>${esc(c.title)}</b>${c.project ? ` <span class="text-slate-400">· ${esc(c.project)}</span>` : ''}`, c.block_id]),
    ...d.reminders.filter(r => r.sent).map(r => [r.time, 'sent', `Reminder: ${esc(r.title)}`, r.block_id]),
  ].sort((a, b) => a[0].localeCompare(b[0]));
  const total = (d.focus || []).reduce((n, f) => n + f.focus_min, 0);
  openModal(`
    <p class="hud-label mb-2">${ico('timer')}What happened</p>
    <h2 class="text-lg font-bold text-slate-800">${esc(fmtDay(date))}</h2>
    <p class="text-sm text-slate-500 mt-1">${total ? `${fmtMin(total)} of focus · ` : ''}${(d.finished || []).length} finished</p>
    <ul class="mt-4 space-y-1.5 max-h-[60vh] overflow-y-auto scroll-thin -mx-1 px-1">
      ${rows.length ? rows.map(([t, tone, html, id]) => `
        <li class="flex items-start gap-2.5 rounded-xl border-l-2 px-3 py-2 text-sm ${CAL_TONE[tone]} ${id ? 'cursor-pointer hover:brightness-105' : ''}" ${id ? `data-ch-open="${id}"` : ''}>
          <span class="hud-num text-xs w-10 shrink-0 pt-0.5">${esc(t)}</span><span class="min-w-0 flex-1 text-slate-700">${html}</span></li>`).join('')
        : '<li class="text-sm text-slate-400 py-4 text-center">Nothing recorded this day.</li>'}
    </ul>
    <div class="flex justify-end mt-5"><button data-close class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">Close</button></div>`, root => {
    root.querySelectorAll('[data-ch-open]').forEach(li => li.onclick = () => openBlockPopup(Number(li.dataset.chOpen)));
    stagger(root.querySelectorAll('li'), 'pop-in', 25, 300);
  });
}

// Add (day, start) or edit (item) one thing on the plan: an event, a to-do from your projects, or a break.
function calEditor({ day, start, item }) {
  const raw = item?.raw, editing = !!raw;
  let kind = raw?.kind || 'event';
  const cands = cal.data.candidates;
  const lengths = [...new Set([...CAL_LENGTHS, raw?.minutes].filter(Boolean))].sort((a, b) => a - b);
  const kinds = [['event', 'Event', 'An appointment, a class, a call…'], ['todo', 'To-do', 'One of your to-dos, at a time'], ['break', 'Break', 'Time off']];
  openModal(`
    <p class="hud-label mb-2">${ico('calendar')}${editing ? 'On your calendar' : 'Add to your calendar'}</p>
    <h2 class="text-lg font-bold text-slate-800 mb-4">${editing ? esc(item.title) : esc(fmtDay(day))}</h2>
    ${editing && item.agent ? `<p class="-mt-3 mb-4 text-xs text-lilac-700">✦ Put here by ${esc(item.agent)}</p>` : ''}
    <div class="inline-flex rounded-full border border-lavender-200 p-0.5 text-xs font-semibold font-display uppercase tracking-wide mb-4" ${editing && raw.block_id ? 'hidden' : ''}>
      ${kinds.map(([k, l, t]) => `<button type="button" data-kind="${k}" class="rounded-full px-3 py-1" title="${t}">${l}</button>`).join('')}
    </div>
    <div data-for="event break">
      <label class="block text-sm font-medium text-slate-700 mb-1" for="ce-title">What</label>
      <input id="ce-title" maxlength="200" value="${esc(raw && !raw.block_id ? raw.title : '')}" placeholder="e.g. Dentist, Call with Sam, Gym" class="w-full rounded-lg border border-slate-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300">
    </div>
    <div data-for="todo">
      ${editing && raw.block_id ? `<p class="text-sm text-slate-600"><a href="#/b/${raw.block_id}" class="font-semibold text-lavender-700 hover:underline">${esc(item.title)}</a>${raw.project ? ` <span class="text-slate-400">· ${esc(raw.project)}</span>` : ''}</p>` : `
      <label class="block text-sm font-medium text-slate-700 mb-1" for="ce-todo">Which to-do</label>
      <select id="ce-todo" class="w-full rounded-lg border border-slate-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300">
        ${cands.length ? cands.map(c => `<option value="${c.id}" data-min="${c.suggested_min}">${esc(c.title)} · ${esc(c.project)}</option>`).join('') : '<option value="">No to-dos ready or due</option>'}
      </select>`}
    </div>
    <div class="grid grid-cols-3 gap-3 mt-4">
      <label class="block"><span class="block text-sm font-medium text-slate-700 mb-1">Day</span>
        <input id="ce-day" type="date" value="${esc(item?.date || day)}" class="w-full rounded-lg border border-slate-300 px-2 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300"></label>
      <label class="block"><span class="block text-sm font-medium text-slate-700 mb-1">Starts</span>
        <input id="ce-start" type="time" step="900" value="${esc(raw?.start || start)}" class="w-full rounded-lg border border-slate-300 px-2 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300"></label>
      <label class="block"><span class="block text-sm font-medium text-slate-700 mb-1">For</span>
        <select id="ce-min" class="w-full rounded-lg border border-slate-300 px-2 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300">
          ${lengths.map(m => `<option value="${m}" ${m === (raw?.minutes || 30) ? 'selected' : ''}>${fmtMin(m)}</option>`).join('')}
        </select></label>
    </div>
    <div class="flex flex-wrap items-center gap-2 mt-6">
      ${editing ? `<button data-del class="rounded-lg px-3 py-2 text-blush-700 hover:bg-blush-50">${ico('trash')} Remove</button>` : ''}
      ${editing && raw.block_id && !raw.done && !raw.gone && item.date === calToday() ? '<button data-focus class="rounded-full border border-lilac-200 px-3 py-1.5 text-sm font-bold text-lilac-700 hover:bg-lilac-50" title="Put it on the focus timer">▶ Focus</button>' : ''}
      <span class="flex-1"></span>
      <button data-close class="rounded-lg px-4 py-2 text-slate-600 hover:bg-slate-100">Cancel</button>
      <button data-save class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">${editing ? 'Save' : 'Add'}</button>
    </div>`, root => {
    const $ = s => root.querySelector(s);
    const paint = () => {
      root.querySelectorAll('[data-kind]').forEach(b => {
        const on = b.dataset.kind === kind;
        b.className = `rounded-full px-3 py-1 ${on ? 'bg-lavender-700 text-white' : 'text-slate-500 hover:text-lavender-700'}`;
      });
      root.querySelectorAll('[data-for]').forEach(el => el.classList.toggle('hidden', !el.dataset.for.split(' ').includes(kind)));
      if (kind === 'break' && !$('#ce-title').value) $('#ce-title').placeholder = 'Break';
    };
    root.querySelectorAll('[data-kind]').forEach(b => b.onclick = () => {
      kind = b.dataset.kind; paint();
      animate(root.querySelector(`[data-for~="${kind}"]`), 'expand');
      if (kind === 'todo') syncLen(); else $('#ce-title').focus();
    });
    const syncLen = () => {                     // a to-do's suggested length
      const o = $('#ce-todo')?.selectedOptions[0];
      if (!o?.dataset.min || editing) return;
      const m = Number(o.dataset.min), sel = $('#ce-min');
      if (![...sel.options].some(x => Number(x.value) === m)) sel.insertAdjacentHTML('beforeend', `<option value="${m}">${fmtMin(m)}</option>`);
      sel.value = String(m);
    };
    $('#ce-todo')?.addEventListener('change', syncLen);
    paint();
    const save = async () => {
      const body = { day: $('#ce-day').value, start: $('#ce-start').value, minutes: Number($('#ce-min').value) };
      if (!body.day || !body.start) return toast('Pick a day and a time.', 'error');
      if (kind === 'todo' && !editing) {
        if (!$('#ce-todo').value) return toast('There’s no to-do to pick: add an event instead.', 'error');
        body.block_id = Number($('#ce-todo').value);
      } else if (!raw?.block_id) {
        body.title = $('#ce-title').value.trim();
        if (!body.title && kind === 'event') { $('#ce-title').focus(); return; }
      }
      body.kind = raw?.block_id ? 'todo' : kind;
      closeModal();
      if (!editing) return calAddItem(body);
      const changes = { start: body.start, minutes: body.minutes, kind: body.kind };
      if (body.day !== item.date) changes.day = body.day;
      if (!raw.block_id) changes.title = body.title || (kind === 'break' ? 'Break' : raw.title);
      calMovePlan(item, changes);
    };
    $('[data-save]').onclick = save;
    root.querySelectorAll('input').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } }));
    $('[data-del]')?.addEventListener('click', async () => {
      const r = await attempt(() => api('DELETE', `/api/calendar/items/${item.date}/${encodeURIComponent(raw.id)}`));
      if (!r) return;
      closeModal();
      const el = app.querySelector(`[data-cal-item="${CSS.escape(item.key)}"]`);
      if (el) await animateOut([el], 'poof', 220);
      toast(`Took “${item.title}” off ${fmtDay(item.date)}.`, 'success', { label: 'Undo', run: undoLast });
      renderCalendar();
    });
    $('[data-focus]')?.addEventListener('click', () => { closeModal(); setTimerTask(raw.block_id, item.title, raw.project); location.hash = '#/'; });
    if (!editing) (kind === 'todo' ? $('#ce-todo') : $('#ce-title'))?.focus();
  });
}

// ---------------------------------------------------------------- dragging
// One pointer-driven drag for everything (mouse, pen and touch; on touch, hold for a moment first so the
// page can still scroll): a ghost follows the pointer, the time it would land on shows in the column, and
// day cells light up. Dropping on a time sets the time; on "All day" (or a month cell) only the day.
function calPointerDown(e) {
  if (e.button !== 0 || cal.drag) return;
  const el = e.currentTarget, resize = e.target.closest('[data-cal-resize]');
  if (e.target.closest('button') && !resize) return;
  const item = el.dataset.calItem ? calFind(el.dataset.calItem) : null;
  const cand = el.dataset.calCand ? cal.data.candidates.find(c => c.id === Number(el.dataset.calCand)) : null;
  if (!item && !cand) return;
  const rect = el.getBoundingClientRect();
  const d = { el, item, cand, resize: !!resize, x0: e.clientX, y0: e.clientY, id: e.pointerId, started: false,
    offY: e.clientY - rect.top, w: rect.width, touch: e.pointerType === 'touch', target: null, ready: e.pointerType !== 'touch' };
  if (d.touch) d.hold = setTimeout(() => { d.ready = true; calDragStart(d, e); }, 320);
  cal.pending = d;
  window.addEventListener('pointermove', calPointerMove);
  window.addEventListener('pointerup', calPointerUp);
  window.addEventListener('pointercancel', calPointerUp);
}

function calDragStart(d, e) {
  d.started = true;
  cal.drag = d;
  if (typeof touch !== 'undefined') touch = null;      // not a swipe or pull-to-refresh (mobile.js)
  if (d.resize) { d.el.classList.add('is-resizing'); d.h0 = d.el.offsetHeight; return; }
  const ghost = d.el.cloneNode(true);
  ghost.classList.add('cal-ghost');
  ghost.removeAttribute('data-cal-item');
  Object.assign(ghost.style, { width: `${Math.min(Math.max(d.w, 140), 260)}px`, height: d.item?.kind === 'plan' ? `${Math.max(d.item.dur / 60 * CAL_H, 24)}px` : 'auto', left: '0px', top: '0px' });
  document.body.appendChild(ghost);
  d.ghost = ghost;
  d.el.classList.add('dragging');
  document.body.classList.add('cal-dragging');
  if (navigator.vibrate && d.touch) navigator.vibrate(12);
  calPointerMove(e);
}

function calPointerMove(e) {
  const d = cal.pending;
  if (!d || e.pointerId !== d.id) return;
  if (!d.started) {
    const far = Math.hypot(e.clientX - d.x0, e.clientY - d.y0) > (d.touch ? 8 : 4);
    if (d.touch && far && !d.ready) { calCleanup(); return; }      // it was a scroll
    if (!far || !d.ready) return;
    calDragStart(d, e);
    return;
  }
  e.preventDefault();
  if (d.resize) {
    const h = Math.max(CAL_SNAP / 60 * CAL_H, d.h0 + e.clientY - d.y0);
    const minutes = Math.max(CAL_SNAP, Math.round(h / CAL_H * 60 / CAL_SNAP) * CAL_SNAP);
    d.minutes = Math.min(minutes, 1440 - d.item.start);
    d.el.style.height = `${d.minutes / 60 * CAL_H}px`;
    const sub = d.el.querySelector('.cal-ev-sub, .cal-ev-time');
    if (sub) sub.textContent = `${calHM(d.item.start)}–${calEnd(calHM(d.item.start), d.minutes)} · ${fmtMin(d.minutes)}`;
    return;
  }
  d.ghost.style.transform = `translate(${e.clientX - 18}px, ${e.clientY - Math.min(d.offY, 18)}px) rotate(-1.5deg)`;
  d.ghost.style.visibility = 'hidden';
  const under = document.elementFromPoint(e.clientX, e.clientY);
  d.ghost.style.visibility = '';
  const col = under?.closest('[data-cal-col]'), dayEl = !col && under?.closest('[data-cal-drop-day]');
  app.querySelectorAll('.cal-drop-on').forEach(x => x !== dayEl && x.classList.remove('cal-drop-on'));
  app.querySelector('.cal-preview')?.remove();
  d.target = null;
  if (col) {
    const dur = d.item?.kind === 'plan' ? d.item.dur : d.cand ? d.cand.suggested_min : 30;
    const y = e.clientY - col.getBoundingClientRect().top - (d.item?.kind === 'plan' ? Math.min(d.offY, dur / 60 * CAL_H) : 0);
    const m = Math.max(0, Math.min(1440 - CAL_SNAP, Math.round(y / CAL_H * 60 / CAL_SNAP) * CAL_SNAP));
    d.target = { day: col.dataset.calCol, start: calHM(m) };
    col.insertAdjacentHTML('beforeend', `<div class="cal-preview" style="top:${m / 60 * CAL_H}px;height:${Math.max(dur, 20) / 60 * CAL_H}px">${calHM(m)}${d.item?.kind === 'plan' || d.cand ? `–${calEnd(calHM(m), dur)}` : ''}</div>`);
  } else if (dayEl) {
    dayEl.classList.add('cal-drop-on');
    d.target = { day: dayEl.dataset.calDropDay, allDay: dayEl.hasAttribute('data-cal-allday') };
  }
  const scroller = app.querySelector('[data-cal-scroll]');           // keep scrolling near the edges
  if (scroller && col) {
    const r = scroller.getBoundingClientRect();
    if (e.clientY < r.top + 30) scroller.scrollTop -= 12;
    else if (e.clientY > r.bottom - 30) scroller.scrollTop += 12;
  }
}

function calCleanup() {
  const d = cal.pending;
  clearTimeout(d?.hold);
  window.removeEventListener('pointermove', calPointerMove);
  window.removeEventListener('pointerup', calPointerUp);
  window.removeEventListener('pointercancel', calPointerUp);
  d?.ghost?.remove();
  d?.el.classList.remove('dragging', 'is-resizing');
  document.body.classList.remove('cal-dragging');
  app.querySelectorAll('.cal-drop-on').forEach(x => x.classList.remove('cal-drop-on'));
  app.querySelector('.cal-preview')?.remove();
  cal.pending = null; cal.drag = null;
  return d;
}

async function calPointerUp(e) {
  const d = cal.pending;
  if (!d || e.pointerId !== d.id) return;
  const started = d.started;
  calCleanup();
  if (!started) return;                                   // a plain click: the click handler opens it
  cal.justDragged = Date.now();
  if (e.type === 'pointercancel') return renderCalendar();
  const { item, cand, target } = d;
  if (d.resize) {
    if (d.minutes && d.minutes !== item.dur) return calMovePlan(item, { minutes: d.minutes });
    return renderCalendar();
  }
  if (!target) return;
  if (cand) {
    if (target.start) return calAddItem({ day: target.day, start: target.start, minutes: cand.suggested_min, block_id: cand.id });
    return calMoveDue(cand.id, cand.title, target.day);
  }
  if (item.kind === 'plan') {
    const changes = {};
    const col = target.start && app.querySelector(`[data-cal-col="${target.day}"]`);
    if (col) {                                    // it moves there now; the redraw after saving confirms it
      Object.assign(d.el.style, { top: `${calMin(target.start) / 60 * CAL_H}px`, left: '2px', width: 'calc(100% - 4px)' });
      col.appendChild(d.el);
    }
    if (target.day !== item.date) changes.day = target.day;
    if (target.start && target.start !== item.raw.start) changes.start = target.start;
    if (!Object.keys(changes).length) return;
    return calMovePlan(item, changes);
  }
  if (item.kind === 'due') {
    const time = target.start || (target.allDay ? null : undefined);
    if (target.day === item.date && (time === undefined || time === (item.raw.time || null))) return;
    return calMoveDue(item.raw.block_id, item.title, target.day, time);
  }
}

// While a touch drag is on, the finger moves the item, not the page.
document.addEventListener('touchmove', e => { if (cal.drag) e.preventDefault(); }, { passive: false });

// The "now" line moves along every minute.
setInterval(() => {
  const line = app.querySelector('[data-cal-now]');
  if (!line) return;
  const now = new Date();
  if (line.closest('[data-cal-col]')?.dataset.calCol !== isoDay(now)) return renderCalendar();
  line.style.top = `${(now.getHours() * 60 + now.getMinutes()) / 60 * CAL_H}px`;
}, 60e3);

// ---------------------------------------------------------------- keyboard: ← → move, T today, D W M views, N add, H history, G go to
document.addEventListener('keydown', e => {
  if (!location.hash.startsWith('#/calendar') || e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
  const el = document.activeElement;
  if (el && (el.matches('input, textarea, select') || el.isContentEditable)) return;
  if (modalRoot.querySelector('[data-backdrop]:not(.closing)')) return;
  const k = e.key.toLowerCase();
  if (k === 'arrowleft' || k === 'arrowright') { e.preventDefault(); calStep(k === 'arrowleft' ? -1 : 1); }
  else if (k === 't') { e.preventDefault(); app.querySelector('[data-cal-today]')?.click(); }
  else if ({ d: 'day', w: 'week', m: 'month' }[k]) { e.preventDefault(); const m = { d: 'day', w: 'week', m: 'month' }[k]; if (m !== cal.mode) calGo({ mode: m }); }
  else if (k === 'n') { e.preventDefault(); app.querySelector('[data-cal-add]')?.click(); }
  else if (k === 'h') { e.preventDefault(); app.querySelector('[data-cal-history]')?.click(); }
  else if (k === 'g') { e.preventDefault(); calJump(); }
});
