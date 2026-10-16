// Agent's Work (#/work): the tasks your agent runs by itself on a schedule ("brief me every morning at 7"),
// made here or by asking the agent in the chat (MCP schedule_task). Each task shows its schedule, next run,
// last result and history; run it now, pause, edit or delete it. Finished one-offs and deleted tasks go to the
// archive at the bottom, where they can be restored or deleted forever. Uses app.js, editor.js (openModal), hud.js
// (panel) and chat.js (md, setChatOpen). Server side: nestwork/agentwork.py.

// How many tasks and results show before "Show more"; back to this each time you come to the tab.
const AW_PAGE = { jobs: 5, runs: 8, archived: 5 };
const aw = { open: new Set(), data: null, shown: { ...AW_PAGE } };
const AW_STATUS = {
  ok: ['Done', 'bg-mint-100 text-mint-700', 'text-mint-500'],
  failed: ['Failed', 'bg-blush-100 text-blush-700', 'text-blush-500'],
  stopped: ['Stopped', 'bg-slate-100 text-slate-600', 'text-slate-400'],
  skipped: ['Skipped', 'bg-slate-100 text-slate-500', 'text-slate-300'],
  running: ['Running', 'bg-baby-100 text-baby-700', 'text-baby-500'],
};
const AW_IDEAS = [
  { name: 'Morning brief', schedule: '0 7 * * *', prompt: 'Write my morning brief. Use today and whats_left to see what’s on today, what’s overdue or due in the next 3 days and what’s blocked; check my calendar for today if you can; look at focus_stats for how yesterday went. End with the one thing I should start with. Keep it short and easy to scan.' },
  { name: 'Evening wrap-up', schedule: '0 21 * * *', prompt: 'Wrap up my day: what I finished today, how much I focused (focus_stats), and what’s waiting for tomorrow morning. Two short paragraphs, warm and encouraging.' },
  { name: 'Deadline radar', schedule: '0 9 * * 1-5', prompt: 'Look for deadlines in the next 7 days across all my projects. For each, say how much is left inside it and whether it looks on track. If nothing is due, just say so in one line.' },
  { name: 'Weekly plan', schedule: '0 18 * * 0', prompt: 'Help me plan next week: read weekly_review, say what went well and what slipped, and suggest the three most important things to do next week, with a day for each.' },
];

// "2026-10-13T07:00" → "Tomorrow 07:00" / "Wed 14 Oct 07:00", plus "in 21 h".
function awWhen(iso) {
  if (!iso) return '';
  const t = new Date(iso.length === 16 ? iso + ':00' : iso), now = new Date();
  const day = new Date(t); day.setHours(0, 0, 0, 0);
  const today = new Date(now); today.setHours(0, 0, 0, 0);
  const diff = Math.round((day - today) / 864e5);
  const hm = t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  const d = diff === 0 ? 'Today' : diff === 1 ? 'Tomorrow' : diff === -1 ? 'Yesterday'
    : t.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
  return `${d} ${hm}`;
}
function awRel(iso) {
  if (!iso) return '';
  const min = Math.round((new Date(iso.length === 16 ? iso + ':00' : iso) - Date.now()) / 6e4);
  const a = Math.abs(min);
  const s = a < 1 ? 'now' : a < 60 ? `${a} min` : a < 48 * 60 ? `${Math.round(a / 60)} h` : `${Math.round(a / 1440)} days`;
  return s === 'now' ? 'now' : min > 0 ? `in ${s}` : `${s} ago`;
}
function awDuration(r) {
  if (!r.finished_at) return '';
  const s = Math.max(0, Math.round((new Date(r.finished_at) - new Date(r.started_at)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
// Pictures a run made (selfies, drawings): click to open full size.
const awPics = r => r.images?.length ? `<div class="mt-2 flex flex-wrap gap-2">${r.images.map(u => `<button type="button" data-aw-pic="${esc(u)}" class="block rounded-xl overflow-hidden border border-lavender-100 max-w-[10rem]"><img src="${esc(u)}" alt="Picture from this run" loading="lazy" class="block max-h-40 w-auto object-cover"></button>`).join('')}</div>` : '';
document.addEventListener('click', e => {
  const pic = e.target.closest('[data-aw-pic]');
  if (pic) openLightbox(pic.dataset.awPic);
});
const awChip = status => { const [label, cls] = AW_STATUS[status] || AW_STATUS.failed; return `<span class="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${cls}">${label}</span>`; };

async function renderAgentWork() {
  state.data = null;
  if (motion.page !== 'work') aw.shown = { ...AW_PAGE };   // coming back to the tab: start short again
  const data = aw.data = await api('GET', '/api/agent-work');
  document.title = 'Agent’s Work · Nestwork';
  const agentName = chat?.agentName || 'your agent';
  const active = data.jobs.filter(j => j.enabled).length;
  const nextJob = data.jobs.filter(j => j.enabled && j.next_run).sort((a, b) => a.next_run.localeCompare(b.next_run))[0];

  const scrolls = Object.fromEntries([...app.querySelectorAll('[data-aw-scroll]')].map(el => [el.dataset.awScroll, el.scrollTop]));
  app.innerHTML = `
    <div class="one-screen-fill flex flex-col gap-3">
      <div class="shrink-0 flex flex-wrap items-end justify-between gap-3">
        <div class="min-w-0">
          <h1 class="text-2xl font-bold text-slate-900">Agent’s Work</h1>
          <p class="text-sm text-slate-500">What ${esc(agentName)} does by itself, on a schedule. Ask in the chat (“brief me every morning at 7”) or add a task here; results come into the chat and stay here.</p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button data-aw-ask class="rounded-full border border-lilac-300 bg-lilac-50 px-4 py-2 text-sm font-semibold text-lilac-700 hover:bg-lilac-100">✦ Ask my agent</button>
          <button data-aw-new class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2 shadow-sm">+ New task</button>
        </div>
      </div>

      ${data.jobs.length ? `
        <div class="shrink-0 grid grid-cols-3 gap-2 sm:max-w-xl">
          ${[[active, 'active'], [nextJob ? awRel(nextJob.next_run) : '–', nextJob ? `next: ${nextJob.name}` : 'nothing coming'],
             [data.runs.filter(r => r.status === 'ok' && Date.now() - new Date(r.started_at) < 7 * 864e5).length, 'results this week']]
            .map(([v, l]) => `<div class="hud-panel hud-in !p-3"><p class="hud-label !pl-1 truncate">${esc(l)}</p><p class="hud-num mt-1.5 text-xl font-extrabold text-slate-900 leading-none truncate">${esc(String(v))}</p></div>`).join('')}
        </div>` : ''}

      <!-- Both columns are the window's height; every list scrolls inside its own box, never the page. -->
      <div class="aw-cols flex-1 min-h-0 grid gap-3 lg:grid-cols-3">
        <div class="hud-col lg:col-span-2">
          ${panel('Scheduled tasks', data.jobs.length ? `<div class="aw-scroll hud-scroll space-y-2.5" data-aw-scroll="jobs">${data.jobs.slice(0, aw.shown.jobs).map(awJobCard).join('')}${awMore('jobs', data.jobs.length)}</div>` : `<div class="aw-scroll hud-scroll">${awEmpty()}</div>`,
            { grow: true, count: data.jobs.length || null, led: data.jobs.some(j => j.running) ? { cls: 'live text-baby-500', title: 'Running now' } : null })}
          ${data.archived.length ? awArchive(data.archived) : ''}
        </div>
        ${panel('Recent results', data.runs.length ? `<ul class="aw-scroll hud-scroll space-y-1.5" data-aw-scroll="runs">${data.runs.slice(0, aw.shown.runs).map(awRunItem).join('')}${awMore('runs', data.runs.length)}</ul>`
          : '<p class="text-sm text-slate-400">Nothing has run yet. Results show up here and in the chat.</p>', { grow: true, count: data.runs.length || null })}
      </div>
    </div>`;
  app.querySelectorAll('[data-aw-scroll]').forEach(el => { el.scrollTop = scrolls[el.dataset.awScroll] || 0; });
  awBind();
  // Same entrance as the HUD and Focus tabs: panels boot in one after another, then the cards slide in.
  if (enterPage('work', 0)) {
    stagger(app.querySelectorAll('.hud-in'), 'pop-in', 75, 700);
    app.querySelectorAll('.aw-job, .aw-run').forEach((el, i) => animate(el, 'pop-in', 260 + Math.min(i * 45, 450)));
  } else if (aw.justOpened) {
    const el = app.querySelector(`[data-aw-body="${aw.justOpened}"]`);
    if (el) animate(el, 'expand');
  }
  aw.justOpened = null;
}

// "Show more" under a list that's cut short, with how many are left.
function awMore(list, total) {
  const left = total - aw.shown[list];
  if (left <= 0) return '';
  const tag = list === 'runs' ? 'li' : 'div';
  return `<${tag}><button data-aw-more="${list}" class="mt-2 w-full rounded-xl border border-dashed border-lavender-200 py-1.5 text-xs font-semibold text-lavender-700 hover:bg-lavender-50">Show more (${left} more)</button></${tag}>`;
}

function awEmpty() {
  return `<div class="text-center py-6">
      <div class="mx-auto mb-3 w-12 h-12 rounded-2xl bg-lavender-100 text-lavender-700 flex items-center justify-center">${ico('bot')}</div>
      <p class="font-semibold text-slate-700">No scheduled tasks yet</p>
      <p class="mt-1 text-sm text-slate-500">Start from an idea, or tell your agent in the chat what to do and when.</p>
      <div class="mt-4 grid sm:grid-cols-2 gap-2 text-left">
        ${AW_IDEAS.map((t, i) => `<button data-aw-idea="${i}" class="text-left rounded-xl border border-slate-200 p-3 hover:border-lavender-300 hover:bg-lavender-50/40">
          <span class="block text-sm font-semibold text-slate-800">${esc(t.name)}</span>
          <span class="block text-[11px] text-lavender-700">🔁 ${esc(awDescribe(t.schedule))}</span>
          <span class="mt-1 text-xs text-slate-500 line-clamp-2">${esc(t.prompt)}</span></button>`).join('')}
      </div>
    </div>`;
}

function awJobCard(j) {
  const last = j.last_run, open = aw.open.has(`j${j.id}`);
  const led = j.running ? 'live text-baby-500' : j.enabled ? 'text-lavender-500' : 'text-slate-300';
  const by = j.created_by && j.created_by !== 'you' ? `<span class="rounded-full bg-lilac-50 border border-lilac-200 px-2 py-0.5 text-[10px] font-semibold text-lilac-700" title="Your agent set this up">✦ by ${esc(j.created_by)}</span>` : '';
  const when = j.running ? '<span class="text-baby-700 font-semibold">Running now…</span>'
    : j.enabled && j.next_run ? `Next: <span class="font-semibold text-slate-700">${esc(awWhen(j.next_run))}</span> <span class="text-slate-400">(${esc(awRel(j.next_run))})</span>`
    : j.schedule.startsWith('once') && j.run_count ? 'Done: it was a one-off' : '<span class="text-slate-500">Paused</span>';
  const btn = (attr, label, cls = 'border-slate-300 text-slate-600 hover:bg-slate-50') => `<button ${attr} class="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-semibold ${cls}">${label}</button>`;
  return `<article data-job="${j.id}" class="aw-job rounded-2xl border ${j.enabled ? 'border-lavender-200 bg-white' : 'border-slate-200 bg-slate-50/60'} p-3.5">
    <div class="flex items-start gap-2.5">
      <span class="hud-led mt-2 ${led}" title="${j.running ? 'Running now' : j.enabled ? 'Active' : 'Paused'}"></span>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-1.5">
          <h3 class="font-bold text-slate-800 truncate ${j.enabled ? '' : 'text-slate-500'}">${esc(j.name)}</h3>${by}
          ${j.deliver_chat ? '' : '<span class="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500" title="Results only show here">not posted to chat</span>'}
          ${j.notify ? `<span class="rounded-full bg-butter-50 border border-butter-200 px-2 py-0.5 text-[10px] text-butter-700" title="Notification sound when it’s done">${j.notify === 'none' ? '🔇 silent' : `🔔 ${esc(j.notify_label)}`}</span>` : ''}
        </div>
        <p class="mt-0.5 text-xs text-slate-500"><span class="text-lavender-700">🔁 ${esc(j.describe)}</span> · ${when}</p>
        <p class="mt-1.5 text-sm text-slate-600 line-clamp-2 whitespace-pre-line" title="${esc(j.prompt)}">${esc(j.prompt)}</p>
      </div>
      <label class="shrink-0 inline-flex items-center gap-1.5 cursor-pointer text-[11px] text-slate-500" title="${j.enabled ? 'Pause it' : 'Resume it'}">
        <input type="checkbox" data-aw-toggle class="sr-only peer" ${j.enabled ? 'checked' : ''}>
        <span class="relative w-8 h-[18px] rounded-full bg-slate-300 peer-checked:bg-lavender-600 transition after:absolute after:top-[2px] after:left-[2px] after:w-[14px] after:h-[14px] after:rounded-full after:bg-white after:transition peer-checked:after:translate-x-[14px]"></span>
      </label>
    </div>
    ${last ? `<div class="mt-2.5 rounded-xl bg-slate-50 border border-slate-200">
      <button data-aw-expand="j${j.id}" class="w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs text-slate-500">
        ${awChip(last.status)}<span class="truncate">Last run ${esc(awWhen(last.started_at))}${last.trigger === 'manual' ? ' (by hand)' : ''}${awDuration(last) ? ` · ${awDuration(last)}` : ''}</span>
        <span class="ml-auto shrink-0 text-lavender-700 font-semibold">${open ? 'Hide' : 'Show result'}</span>
      </button>
      ${open ? `<div data-aw-body="j${j.id}" class="chat-md aw-result px-3 pb-3 text-sm text-slate-700 break-words">${md(last.output || '')}${awPics(last)}</div>` : ''}
    </div>` : ''}
    <div class="mt-2.5 flex flex-wrap gap-1.5">
      ${j.running ? btn('data-aw-stop', '■ Stop', 'border-blush-200 text-blush-700 hover:bg-blush-50') : btn('data-aw-run', '▶ Run now', 'border-lavender-300 text-lavender-700 hover:bg-lavender-50')}
      ${btn('data-aw-edit', 'Edit')}
      ${j.run_count > 1 ? btn('data-aw-history', `History (${j.run_count})`) : ''}
      ${btn('data-aw-delete', '🗑 Delete', 'border-slate-200 text-slate-500 hover:text-blush-700 hover:bg-blush-50')}
    </div>
  </article>`;
}

// Finished one-offs and deleted tasks: folded away, each with Restore, History and Delete forever.
function awArchive(jobs) {
  const open = aw.open.has('archive');
  return `<section class="hud-panel hud-in !p-0 flex flex-col ${open ? 'hud-grow' : 'shrink-0'}">
    <button data-aw-expand="archive" class="shrink-0 w-full flex items-center gap-2 px-3.5 py-2.5 text-left" aria-expanded="${open}">
      <span class="text-slate-400">${open ? '▾' : '▸'}</span>
      <span class="text-sm font-semibold text-slate-600">Archive</span>
      <span class="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">${jobs.length}</span>
      <span class="ml-auto text-[11px] text-slate-400 truncate">Done and deleted tasks</span>
    </button>
    ${open ? `<div data-aw-body="archive" data-aw-scroll="archived" class="aw-scroll hud-scroll border-t border-slate-100">
      <ul class="divide-y divide-slate-100">${jobs.slice(0, aw.shown.archived).map(awArchivedItem).join('')}</ul>
      <div class="px-3 pb-2">${awMore('archived', jobs.length)}</div></div>` : ''}
  </section>`;
}

function awArchivedItem(j) {
  const last = j.last_run, open = aw.open.has(`j${j.id}`);
  const btn = (attr, label, cls = 'border-slate-300 text-slate-600 hover:bg-slate-50') => `<button ${attr} class="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${cls}">${label}</button>`;
  return `<li data-job="${j.id}" class="px-3.5 py-2.5">
    <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
      <h3 class="min-w-0 truncate text-sm font-semibold text-slate-600">${esc(j.name)}</h3>
      <span class="text-[11px] text-slate-400">🔁 ${esc(j.describe)} · archived ${esc(awWhen(j.archived_at))}${j.run_count ? ` · ${j.run_count} run${j.run_count === 1 ? '' : 's'}` : ''}</span>
    </div>
    ${last ? `<button data-aw-expand="j${j.id}" class="mt-1 flex items-center gap-2 text-left text-[11px] text-slate-500">
        ${awChip(last.status)}<span>Last run ${esc(awWhen(last.started_at))}</span><span class="text-lavender-700 font-semibold">${open ? 'Hide' : 'Show result'}</span></button>
      ${open ? `<div data-aw-body="j${j.id}" class="chat-md aw-result mt-1.5 rounded-xl bg-slate-50 border border-slate-200 px-3 py-2 text-sm text-slate-700 break-words">${md(last.output || '')}${awPics(last)}</div>` : ''}` : ''}
    <div class="mt-1.5 flex flex-wrap gap-1.5">
      ${btn('data-aw-restore', '↩ Restore', 'border-lavender-300 text-lavender-700 hover:bg-lavender-50')}
      ${j.run_count > 1 ? btn('data-aw-history', `History (${j.run_count})`) : ''}
      ${btn('data-aw-purge', 'Delete forever', 'border-slate-200 text-slate-500 hover:text-blush-700 hover:bg-blush-50')}
    </div>
  </li>`;
}

function awRunItem(r) {
  const open = aw.open.has(`r${r.id}`), dot = (AW_STATUS[r.status] || AW_STATUS.failed)[2];
  return `<li class="aw-run rounded-xl border border-slate-200 bg-white">
    <button data-aw-expand="r${r.id}" class="w-full flex items-center gap-2 px-2.5 py-1.5 text-left">
      <span class="hud-dot ${r.status === 'running' ? 'live' : ''} ${dot}" style="background:currentColor"></span>
      <span class="min-w-0 flex-1"><span class="block text-sm font-semibold text-slate-700 truncate">${esc(r.job_name)}</span>
      <span class="block text-[11px] text-slate-400">${esc(awWhen(r.started_at))}${r.trigger === 'manual' ? ' · by hand' : ''}${awDuration(r) ? ` · ${awDuration(r)}` : ''}</span></span>
      ${awChip(r.status)}
    </button>
    ${open && r.status !== 'running' ? `<div data-aw-body="r${r.id}" class="chat-md aw-result border-t border-slate-100 px-3 py-2 text-sm text-slate-700 break-words">${md(r.output || '')}${awPics(r)}
      ${r.tools?.length ? `<p class="mt-2 text-[10px] text-slate-400">Used: ${esc(r.tools.join(' · '))}</p>` : ''}</div>` : ''}
  </li>`;
}

function awBind() {
  const jobOf = el => [...aw.data.jobs, ...aw.data.archived].find(j => j.id === Number(el.closest('[data-job]').dataset.job));
  app.querySelector('[data-aw-new]').onclick = () => awEditor();
  app.querySelector('[data-aw-ask]').onclick = () => {
    setChatOpen(true);
    const input = document.getElementById('chat-input');
    if (!input.value.trim()) input.value = 'Every morning at 7, give me a brief of my day: ';
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  };
  app.querySelectorAll('[data-aw-more]').forEach(b => b.onclick = () => {
    const k = b.dataset.awMore;
    aw.shown[k] += AW_PAGE[k];
    renderAgentWork();
  });
  app.querySelectorAll('[data-aw-idea]').forEach(b => b.onclick = () => awEditor(null, AW_IDEAS[b.dataset.awIdea]));
  app.querySelectorAll('[data-aw-expand]').forEach(b => b.onclick = () => {
    const k = b.dataset.awExpand;
    if (aw.open.has(k)) aw.open.delete(k); else { aw.open.add(k); aw.justOpened = k; }
    renderAgentWork();
  });
  app.querySelectorAll('[data-aw-toggle]').forEach(c => c.onchange = async () => {
    const j = jobOf(c);
    const r = await attempt(() => api('PATCH', `/api/agent-work/${j.id}`, { enabled: c.checked }));
    if (r) toast(c.checked ? `“${j.name}” is on again: next run ${awWhen(r.next_run)}.` : `Paused “${j.name}”.`, 'success');
    renderAgentWork();
  });
  app.querySelectorAll('[data-aw-run]').forEach(b => b.onclick = async () => {
    const j = jobOf(b);
    b.disabled = true;
    if (await attempt(() => api('POST', `/api/agent-work/${j.id}/run`))) {
      toast(`Running “${j.name}”. The result comes into the chat.`, 'success');
      aw.open.add(`j${j.id}`);
    }
    renderAgentWork();
  });
  app.querySelectorAll('[data-aw-stop]').forEach(b => b.onclick = async () => {
    await attempt(() => api('POST', `/api/agent-work/${jobOf(b).id}/stop`));
  });
  app.querySelectorAll('[data-aw-edit]').forEach(b => b.onclick = () => awEditor(jobOf(b)));
  app.querySelectorAll('[data-aw-history]').forEach(b => b.onclick = () => awHistory(jobOf(b)));
  app.querySelectorAll('[data-aw-delete]').forEach(b => b.onclick = async () => {
    const j = jobOf(b);
    if (await attempt(() => api('DELETE', `/api/agent-work/${j.id}`))) { toast(`Moved “${j.name}” to the archive.`, 'success'); renderAgentWork(); }
  });
  app.querySelectorAll('[data-aw-restore]').forEach(b => b.onclick = async () => {
    const j = jobOf(b);
    const r = await attempt(() => api('POST', `/api/agent-work/${j.id}/restore`));
    if (r) toast(r.enabled ? `“${j.name}” is back: next run ${awWhen(r.next_run)}.` : `“${j.name}” is back, paused: edit it to give it a new time.`, 'success');
    renderAgentWork();
  });
  app.querySelectorAll('[data-aw-purge]').forEach(b => b.onclick = async () => {
    const j = jobOf(b);
    if (!confirm(`Delete “${j.name}” and its ${j.run_count} result${j.run_count === 1 ? '' : 's'} forever? Results posted in the chat stay there.`)) return;
    if (await attempt(() => api('DELETE', `/api/agent-work/${j.id}?forever=true`))) { toast(`Deleted “${j.name}” for good.`, 'success'); renderAgentWork(); }
  });
}

async function awHistory(j) {
  const runs = await api('GET', `/api/agent-work/${j.id}/runs`);
  openModal(`
    <div class="flex items-start justify-between gap-3 mb-3">
      <div><h2 class="text-lg font-semibold">${esc(j.name)}</h2><p class="text-xs text-slate-500">🔁 ${esc(j.describe)} · ${runs.length} run${runs.length === 1 ? '' : 's'}</p></div>
      <button data-close class="p-1 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="Close">✕</button>
    </div>
    <ul class="space-y-2">${runs.map(r => `<li class="rounded-xl border border-slate-200">
      <details ${r === runs[0] ? 'open' : ''}><summary class="flex items-center gap-2 px-3 py-2 cursor-pointer text-sm">
        ${awChip(r.status)}<span class="text-slate-600">${esc(awWhen(r.started_at))}</span>
        <span class="text-[11px] text-slate-400">${r.trigger === 'manual' ? 'by hand' : 'on schedule'}${awDuration(r) ? ` · ${awDuration(r)}` : ''}${r.cost != null ? ` · $${r.cost.toFixed(3)}` : ''}</span></summary>
        <div class="chat-md border-t border-slate-100 px-3 py-2 text-sm text-slate-700 break-words">${md(r.output || (r.status === 'running' ? 'Still running…' : ''))}${awPics(r)}</div>
      </details></li>`).join('')}</ul>`, null, { wide: false });
}

// ---------------------------------------------------------------- the editor
const AW_DAYS = [[1, 'Mon'], [2, 'Tue'], [3, 'Wed'], [4, 'Thu'], [5, 'Fri'], [6, 'Sat'], [0, 'Sun']];
const pad2 = n => String(n).padStart(2, '0');

// A schedule string → the editor's fields (anything else is "custom").
function awFromSchedule(s) {
  let m;
  if ((m = s.match(/^once (\d{4}-\d\d-\d\d)T(\d\d:\d\d)$/))) return { kind: 'once', date: m[1], time: m[2] };
  if ((m = s.match(/^\*\/(\d+) \* \* \* \*$/))) return { kind: 'minutes', every: m[1] };
  if ((m = s.match(/^(\d+) \*\/(\d+) \* \* \*$/))) return { kind: 'hours', every: m[2], minute: m[1] };
  if ((m = s.match(/^(\d+) (\d+) (\d+) \* \*$/))) return { kind: 'monthly', time: `${pad2(m[2])}:${pad2(m[1])}`, dom: m[3] };
  if ((m = s.match(/^(\d+) (\d+) \* \* (\S+)$/))) {
    const time = `${pad2(m[2])}:${pad2(m[1])}`;
    if (m[3] === '*') return { kind: 'daily', time };
    if (m[3] === '1-5') return { kind: 'weekdays', time };
    if (/^[0-7](,[0-7])*$/.test(m[3])) return { kind: 'weekly', time, days: m[3].split(',').map(d => Number(d) % 7) };
  }
  return { kind: 'custom', cron: s };
}
function awToSchedule(f) {
  const [h, mi] = (f.time || '07:00').split(':').map(Number);
  switch (f.kind) {
    case 'daily': return `${mi} ${h} * * *`;
    case 'weekdays': return `${mi} ${h} * * 1-5`;
    case 'weekly': return `${mi} ${h} * * ${(f.days?.length ? [...f.days].sort() : [1]).join(',')}`;
    case 'monthly': return `${mi} ${h} ${f.dom || 1} * *`;
    case 'hours': return `${Number(f.minute) || 0} */${Math.max(1, Number(f.every) || 1)} * * *`;
    case 'minutes': return `*/${Math.max(1, Number(f.every) || 30)} * * * *`;
    case 'once': return `once ${f.date}T${f.time || '09:00'}`;
    default: return (f.cron || '').trim();
  }
}
// The same words the server uses, for idea cards (no request needed for these simple ones).
function awDescribe(s) {
  const f = awFromSchedule(s);
  return { daily: `Every day at ${f.time}`, weekdays: `Weekdays at ${f.time}`, monthly: `Every month on day ${f.dom} at ${f.time}`,
    weekly: `Every ${(f.days || []).map(d => AW_DAYS.find(x => x[0] === d)?.[1]).join(', ')} at ${f.time}` }[f.kind] || s;
}

async function awEditor(job = null, idea = null) {
  const src = job || idea || {};
  const lib = await soundLib(true);
  const taskDefault = lib.notifications.find(n => n.key === 'task');
  const tomorrow = new Date(Date.now() + 864e5).toLocaleDateString('en-CA');
  const f = { time: '07:00', days: [1], dom: 1, every: 2, minute: 0, date: tomorrow, ...awFromSchedule(src.schedule || '0 7 * * *') };
  const field = 'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-lavender-300';
  openModal(`
    <h2 class="text-lg font-semibold mb-4">${job ? 'Edit task' : 'New scheduled task'}</h2>
    <label class="block text-sm font-medium text-slate-700 mb-1" for="aw-name">Name</label>
    <input id="aw-name" maxlength="120" class="${field}" value="${esc(src.name || '')}" placeholder="e.g. Morning brief">
    <label class="block text-sm font-medium text-slate-700 mt-4 mb-1" for="aw-prompt">What should your agent do each time?</label>
    <textarea id="aw-prompt" rows="5" maxlength="8000" class="${field}" placeholder="e.g. Write my morning brief: what’s on today, what’s due this week, and the one thing to start with.">${esc(src.prompt || '')}</textarea>
    <p class="mt-1 text-[11px] text-slate-400">It won’t remember the chat when it runs, so say everything it needs to know.</p>
    ${!job && !idea ? `<div class="mt-2 flex flex-wrap gap-1.5">${AW_IDEAS.map((t, i) => `<button type="button" data-idea="${i}" class="rounded-full border border-lavender-200 px-2.5 py-0.5 text-[11px] text-slate-600 hover:bg-lavender-50">${esc(t.name)}</button>`).join('')}</div>` : ''}

    <p class="text-sm font-medium text-slate-700 mt-5 mb-1">When</p>
    <div class="grid sm:grid-cols-2 gap-2">
      <select id="aw-kind" class="${field} bg-white">
        ${[['daily', 'Every day'], ['weekdays', 'Weekdays (Mon–Fri)'], ['weekly', 'Some days of the week'], ['monthly', 'Every month'],
           ['hours', 'Every few hours'], ['minutes', 'Every few minutes'], ['once', 'Once'], ['custom', 'Custom (cron)']]
          .map(([k, l]) => `<option value="${k}" ${f.kind === k ? 'selected' : ''}>${l}</option>`).join('')}
      </select>
      <div data-show="daily weekdays weekly monthly once"><input id="aw-time" type="time" class="${field}" value="${esc(f.time)}"></div>
      <div data-show="hours" class="flex items-center gap-2 text-sm text-slate-600">every <input id="aw-every-h" type="number" min="1" max="23" class="${field} w-20" value="${esc(String(f.kind === 'hours' ? f.every : 2))}"> hours at :<input id="aw-minute" type="number" min="0" max="59" class="${field} w-20" value="${esc(String(f.minute))}"></div>
      <div data-show="minutes" class="flex items-center gap-2 text-sm text-slate-600">every <input id="aw-every-m" type="number" min="1" max="59" class="${field} w-20" value="${esc(String(f.kind === 'minutes' ? f.every : 30))}"> minutes</div>
      <div data-show="custom"><input id="aw-cron" class="${field} font-mono" value="${esc(f.cron || '')}" placeholder="min hour day month weekday, e.g. 0 7 * * *"></div>
    </div>
    <div data-show="weekly" class="mt-2 flex flex-wrap gap-1.5">${AW_DAYS.map(([d, l]) => `<button type="button" data-day="${d}" aria-pressed="${f.days.includes(d)}" class="aw-day w-11 rounded-full border px-2 py-1 text-xs font-semibold">${l}</button>`).join('')}</div>
    <div data-show="monthly" class="mt-2 flex items-center gap-2 text-sm text-slate-600">on day <input id="aw-dom" type="number" min="1" max="31" class="${field} w-20" value="${esc(String(f.dom))}"> of the month</div>
    <div data-show="once" class="mt-2"><input id="aw-date" type="date" class="${field} sm:w-1/2" value="${esc(f.date)}"></div>
    <p id="aw-preview" class="mt-2 min-h-[2.5rem] rounded-xl bg-lavender-50/60 border border-lavender-100 px-3 py-2 text-xs text-slate-600"></p>

    <label class="block text-sm font-medium text-slate-700 mt-4 mb-1" for="aw-notify">🔔 Notification when it’s done</label>
    <div class="flex items-center gap-2">
      <select id="aw-notify" class="${field} bg-white">
        <option value="">Default: ${esc(taskDefault?.name || 'Scheduled task done')} (${esc(taskDefault ? soundName(lib, taskDefault.sound) : 'Marimba')})</option>
        ${lib.notifications.filter(n => n.key !== 'task').map(n => `<option value="${n.id}" ${String(src.notify) === String(n.id) ? 'selected' : ''}>${esc(n.name)} (${esc(soundName(lib, n.sound))})</option>`).join('')}
        <option value="none" ${src.notify === 'none' ? 'selected' : ''}>Silent</option>
      </select>
      <button type="button" id="aw-notify-test" class="shrink-0 w-9 h-9 rounded-full border border-lavender-200 text-lavender-700 hover:bg-lavender-50" title="Play it" aria-label="Play it">▶</button>
    </div>
    <p class="mt-1 text-[11px] text-slate-400">Add your own (e.g. “Wake up”) in <button type="button" id="aw-sounds" class="text-lavender-700 underline">Settings → Sounds</button>.</p>

    <label class="mt-4 flex items-center gap-2 text-sm text-slate-700"><input id="aw-chat" type="checkbox" class="rounded" ${src.deliver_chat === false ? '' : 'checked'}> Post each result in the chat</label>
    <div class="flex justify-end gap-2 mt-6">
      <button data-close class="rounded-lg px-4 py-2 text-slate-600 hover:bg-slate-100">Cancel</button>
      <button id="aw-save" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">${job ? 'Save' : 'Schedule it'}</button>
    </div>`, root => {
    const $ = s => root.querySelector(s);
    let schedule = '', valid = false, timer = null;
    const read = () => ({
      kind: $('#aw-kind').value, time: $('#aw-time').value, dom: $('#aw-dom').value, date: $('#aw-date').value,
      every: $('#aw-kind').value === 'hours' ? $('#aw-every-h').value : $('#aw-every-m').value, minute: $('#aw-minute').value,
      cron: $('#aw-cron').value, days: [...root.querySelectorAll('.aw-day[aria-pressed=true]')].map(b => Number(b.dataset.day)),
    });
    const paint = () => {
      const kind = $('#aw-kind').value;
      root.querySelectorAll('[data-show]').forEach(el => el.classList.toggle('hidden', !el.dataset.show.split(' ').includes(kind)));
      root.querySelectorAll('.aw-day').forEach(b => {
        const on = b.getAttribute('aria-pressed') === 'true';
        b.classList.toggle('bg-lavender-700', on); b.classList.toggle('text-white', on); b.classList.toggle('border-lavender-700', on);
        b.classList.toggle('border-slate-300', !on); b.classList.toggle('text-slate-600', !on);
      });
    };
    const preview = () => {
      paint();
      schedule = awToSchedule(read());
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const out = $('#aw-preview');
        try {
          const p = await api('GET', `/api/agent-work/preview?schedule=${encodeURIComponent(schedule)}`);
          valid = p.next.length > 0;
          out.innerHTML = valid ? `<span class="font-semibold text-lavender-800">🔁 ${esc(p.describe)}</span><br>Next: ${p.next.map(t => esc(awWhen(t))).join(' · ')}`
            : '<span class="text-blush-700">That time has already passed.</span>';
        } catch (e) { valid = false; out.innerHTML = `<span class="text-blush-700">${esc(e.message)}</span>`; }
      }, 200);
    };
    root.querySelectorAll('input, select').forEach(el => el.addEventListener('input', preview));
    root.querySelectorAll('.aw-day').forEach(b => b.onclick = () => { b.setAttribute('aria-pressed', String(b.getAttribute('aria-pressed') !== 'true')); preview(); });
    root.querySelectorAll('[data-idea]').forEach(b => b.onclick = () => { closeModal(); awEditor(null, AW_IDEAS[b.dataset.idea]); });
    $('#aw-notify-test').onclick = () => {
      const v = $('#aw-notify').value;
      const n = v === '' ? taskDefault : lib.notifications.find(x => String(x.id) === v);
      if (n) playNotifySound(n.sound, n.volume);
    };
    $('#aw-sounds').onclick = () => { closeModal(); settingsModal('sounds'); };
    preview();
    setTimeout(() => (src.name ? $('#aw-prompt') : $('#aw-name')).focus(), 50);
    $('#aw-save').onclick = async () => {
      const body = { name: $('#aw-name').value.trim(), prompt: $('#aw-prompt').value.trim(), schedule, deliver_chat: $('#aw-chat').checked,
        notify: $('#aw-notify').value };
      if (!body.name) return toast('Give it a name.', 'error');
      if (!body.prompt) return toast('Say what your agent should do.', 'error');
      if (!valid) return toast('Pick a time that’s still to come.', 'error');
      const r = await attempt(() => job ? api('PATCH', `/api/agent-work/${job.id}`, { ...body, enabled: true }) : api('POST', '/api/agent-work', body));
      if (!r) return;
      closeModal();
      toast(`${job ? 'Saved' : 'Scheduled'} “${r.name}”: next run ${awWhen(r.next_run)}.`, 'success');
      if (location.hash.startsWith('#/work')) renderAgentWork(); else location.hash = '#/work';
    };
  });
}
