// The weekly review (#/review): what got done, where the focus time went, what slipped, how each project's
// finish date moved since the last review, what's blocked and what's coming, then a short reflection to
// save. "Review it with my agent" opens the chat with the same numbers (MCP weekly_review). Uses app.js and
// hud.js (panel, ring helpers). Server side: nestwork/review.py.

async function renderReview() {
  state.data = null;
  const r = await api('GET', '/api/review');
  document.title = 'Weekly review · Nestwork';
  const day = iso => new Date(iso + 'T00:00').toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  const f = r.focus, projects = Object.values(r.projects);
  const byProject = r.done.reduce((m, d) => ((m[d.project_title || 'No project'] ||= []).push(d), m), {});
  const maxDay = Math.max(1, ...f.by_day.map(d => d.focus_min));
  const pctTxt = p => `${Math.round((p || 0) * 100)}%`;
  const link = i => `<a href="#/b/${i.id}" class="font-semibold text-slate-700 hover:text-lavender-800">${esc(i.title)}</a>`;
  const empty = text => `<p class="text-sm text-slate-400">${text}</p>`;

  app.innerHTML = `
    <div class="flex flex-col gap-3 pb-8">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 class="text-2xl font-bold text-slate-900">Weekly review</h1>
          <p class="text-sm text-slate-500">${esc(day(r.start))} – ${esc(day(r.end))}. Look back, notice what got in the way, decide one or two changes for next week.${r.last_review ? ` Compared with your review of ${esc(day(r.last_review.created_at.slice(0, 10)))}.` : ''}</p>
        </div>
        <button data-rv-agent class="rounded-full border border-lilac-300 bg-lilac-50 px-4 py-2 text-sm font-semibold text-lilac-700 hover:bg-lilac-100">✦ Review it with my agent</button>
      </div>

      <div class="grid gap-3 lg:grid-cols-3">
        ${panel('Done this week', r.done.length ? Object.entries(byProject).map(([proj, items]) => `
          <p class="mt-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">${esc(proj)} · ${items.length}</p>
          <ul class="space-y-0.5">${items.map(d => `<li class="text-sm text-slate-700 truncate">✓ ${d.block_id ? `<a href="#/b/${d.block_id}" class="hover:text-lavender-800">${esc(d.title)}</a>` : esc(d.title)} <span class="text-[11px] text-slate-400">${esc(day(d.done_at.slice(0, 10)))}</span></li>`).join('')}</ul>`).join('')
          : empty('Nothing finished this week.'), { count: r.done_count })}

        ${panel('Focus', `
          <div class="grid grid-cols-3 gap-2 text-center">
            <div><p class="hud-num text-xl font-extrabold text-slate-800">${esc(fmtMin(f.total_min))}</p><p class="text-[10px] uppercase text-slate-400">focused</p></div>
            <div><p class="hud-num text-xl font-extrabold text-slate-800">${f.sessions}</p><p class="text-[10px] uppercase text-slate-400">sessions</p></div>
            <div><p class="hud-num text-xl font-extrabold text-slate-800">${f.avg_rating ?? '–'}</p><p class="text-[10px] uppercase text-slate-400">felt (of 5)</p></div>
          </div>
          <div class="mt-3 flex items-end gap-1 h-16">${f.by_day.map(d => `<div class="flex-1 flex flex-col items-center gap-0.5" title="${esc(day(d.date))}: ${esc(fmtMin(d.focus_min))}">
            <div class="w-full bg-lavender-300/80" style="height:${Math.max(2, d.focus_min / maxDay * 52)}px"></div>
            <span class="text-[9px] text-slate-400">${new Date(d.date + 'T00:00').toLocaleDateString([], { weekday: 'narrow' })}</span></div>`).join('')}</div>
          <ul class="mt-2 space-y-0.5 text-xs text-slate-600">${f.by_project.slice(0, 5).map(p => `<li class="flex justify-between gap-2"><span class="truncate">${esc(p.title)}</span><span class="hud-num">${esc(fmtMin(p.focus_min))}</span></li>`).join('')}</ul>
          <p class="mt-2 text-[11px] text-slate-400">${f.active_days} active day${f.active_days === 1 ? '' : 's'} · streak ${f.streak} · goal ${esc(fmtMin(f.goal_min))} a day</p>`)}

        ${panel('Slipped', r.slipped.length || r.overdue_older ? `
          <ul class="space-y-1">${r.slipped.map(i => `<li class="text-sm truncate">${link(i)} <span class="text-[11px] text-blush-700">was due ${esc(day(i.due_date))}</span></li>`).join('')}</ul>
          ${r.overdue_older ? `<p class="mt-2 text-xs text-slate-500">Plus ${r.overdue_older} overdue from before this week.</p>` : ''}
          <p class="mt-2 text-xs text-slate-500">For each: still worth doing? Give it a new date, split it, or let it go.</p>`
          : empty('Nothing slipped. 🎉'), { count: r.slipped.length })}

        ${panel('Projects', projects.length ? `<ul class="space-y-2.5">${projects.map(p => {
          const delta = p.progress_before != null ? Math.round((p.progress - p.progress_before) * 100) : null;
          const moved = p.finish && p.finish_before && p.finish !== p.finish_before ? Math.round((new Date(p.finish) - new Date(p.finish_before)) / 864e5) : null;
          return `<li>
            <div class="flex items-center justify-between gap-2 text-sm"><a href="#/b/${p.id}" class="font-bold text-slate-800 truncate hover:text-lavender-800">${esc(p.title)}</a>
              <span class="shrink-0 hud-num text-xs text-slate-500">${pctTxt(p.progress)}${delta ? ` <span class="${delta > 0 ? 'text-mint-700' : 'text-blush-700'}">${delta > 0 ? '+' : ''}${delta}</span>` : ''}</span></div>
            <div class="mt-1">${bar(p.progress, 'h-1.5')}</div>
            <p class="mt-0.5 text-[11px] text-slate-500">${p.done_this_week} done this week${p.finish ? ` · done around ${esc(day(p.finish))}` : ''}${moved ? ` <span class="${moved > 0 ? 'text-blush-700' : 'text-mint-700'}">(${moved > 0 ? `${moved} days later` : `${-moved} days sooner`} than last review)</span>` : ''}${p.late_days > 0 ? ` · <span class="text-blush-700">${p.late_days} days after its due date</span>` : ''}</p>
          </li>`; }).join('')}</ul>` : empty('No projects.'))}

        ${panel('Blocked', r.blocked.length ? `<ul class="space-y-1">${r.blocked.map(i => `<li class="text-sm"><span class="truncate">${link(i)}</span><span class="block text-[11px] text-slate-500 truncate">waits until ${esc(i.waiting_for.join(' and '))}</span></li>`).join('')}</ul>
          <p class="mt-2 text-xs text-slate-500">Is the thing they wait for moving? If not, it may be the one to start next.</p>` : empty('Nothing blocked.'), { count: r.blocked_count })}

        ${panel('Coming up · 7 days', r.coming.length ? `<ul class="space-y-1">${r.coming.map(i => `<li class="flex items-center gap-2 text-sm min-w-0"><span class="shrink-0 w-24 text-[11px] text-slate-500">${esc(day(i.due_date))}${i.due_time ? ` ${esc(i.due_time)}` : ''}</span><span class="truncate">${link(i)}</span></li>`).join('')}</ul>` : empty('Nothing due next week.'))}
      </div>

      ${panel('Reflection', `
        <p class="text-xs text-slate-500">What went well? What got in the way? What will you do differently next week? Saving keeps this week’s numbers, so your next review shows what changed.</p>
        ${r.last_review?.note ? `<p class="mt-2 rounded-xl bg-slate-50 border border-slate-200 px-3 py-2 text-xs text-slate-600"><span class="font-bold">Last time you wrote:</span> ${esc(r.last_review.note)}</p>` : ''}
        <textarea data-rv-note rows="4" class="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" placeholder="e.g. Writing went well in the mornings. Meetings ate Wednesday. Next week: thesis chapter 2 first, emails after lunch."></textarea>
        <div class="mt-2 flex justify-end"><button data-rv-save class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-sm font-semibold px-4 py-2">Save this review</button></div>`)}

      ${r.history.length ? panel('Past reviews', `<ul class="space-y-2">${r.history.map(h => `
        <li class="rounded-xl border border-slate-200 px-3 py-2">
          <p class="text-xs font-semibold text-slate-600">${esc(day(h.period_start))} – ${esc(day(h.period_end))} <span class="font-normal text-slate-400">· ${h.done_count ?? 0} done · ${esc(fmtMin(h.focus_min || 0))} focused</span></p>
          ${h.note ? `<p class="mt-0.5 text-sm text-slate-700 whitespace-pre-line">${esc(h.note)}</p>` : ''}
        </li>`).join('')}</ul>`, { count: r.history.length }) : ''}
    </div>`;

  app.querySelector('[data-rv-save]').onclick = async () => {
    const note = app.querySelector('[data-rv-note]').value;
    if (await attempt(() => api('POST', '/api/review', { note }))) { toast('Review saved ✓', 'success'); renderReview(); }
  };
  app.querySelector('[data-rv-agent]').onclick = () => {
    setChatOpen(true, { focus: false });
    sendMessage('Let’s do my weekly review. Read it with the weekly_review tool, tell me what stands out (good and bad), ask me what got in the way, and help me pick one or two changes and priorities for next week.');
  };
  enterPage('review', 0);
}
