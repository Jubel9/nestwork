// The Focus page (#/focus): your productivity from the Pomodoro timer. Focus time per day and project,
// a daily score, streak, when you focus best, and every session with its rating and note.
// Uses helpers from app.js (api, esc, attempt, toast…), hud.js (panel, hud.timer, rateSession, fmtClock…)
// and chat.js (setChatOpen, sendMessage). Data: GET /api/focus/stats; the agent reads the same through MCP.

const fv = { days: Number(localGet('nestwork.focusDays')) || 7 };
const RANGES = [[7, '7 days'], [30, '30 days'], [90, '90 days']];

// "2 h 05" / "25 min"
function fmtMin(m, short = false) {
  m = Math.round(m || 0);
  if (m < 60) return `${m}${short ? 'm' : ' min'}`;
  return short ? `${Math.floor(m / 60)}h${m % 60 ? ` ${String(m % 60).padStart(2, '0')}` : ''}` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}`;
}

// Projects keep their color whatever the range: slots follow project ids (oldest first), never rank.
// Eight validated series colors; the rest, and anything without a project, are "Other" gray.
function seriesFor(stats, projects) {
  const keys = [...new Set([...projects.map(p => p.id).sort((a, b) => a - b).map(String),
    ...stats.projects.map(p => p.key).filter(k => k !== 'other' && !projects.some(p => String(p.id) === k)).sort()])];
  const names = Object.fromEntries(stats.projects.map(p => [p.key, p.title]));
  projects.forEach(p => { names[String(p.id)] = p.title; });
  names.other = 'Other (no project)';
  const color = k => { const i = keys.indexOf(k); return i >= 0 && i < 8 ? `var(--series-${i + 1})` : 'var(--series-other)'; };
  return { color, name: k => names[k] || 'Other', order: k => (k === 'other' ? 1e9 : keys.indexOf(k)) };
}

async function renderFocus() {
  state.data = null;
  const [st, projects] = await Promise.all([api('GET', `/api/focus/stats?days=${fv.days}`), api('GET', '/api/projects')]);
  fv.stats = st;
  fv.series = seriesFor(st, projects);
  document.title = 'Focus · Nestwork';
  const t = st.today, tot = st.total;

  app.innerHTML = `
    <div class="fv one-screen-fill flex flex-col gap-3">
      <div class="shrink-0 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 class="text-2xl font-bold text-slate-900">Focus</h1>
          <p class="text-sm text-slate-500">From your Pomodoro sessions. Your agent can see this too: ask it how your week went.</p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <a href="#/review" class="rounded-full border border-lavender-200 bg-white/60 px-3 py-1 text-xs font-semibold text-lavender-700 hover:bg-lavender-50" title="Look back at the week and plan the next one">Weekly review →</a>
          <div class="flex rounded-full border border-lavender-200 bg-white/60 p-0.5 text-xs font-semibold" role="group" aria-label="Range">
            ${RANGES.map(([d, l]) => `<button data-fv-days="${d}" class="rounded-full px-3 py-1 ${d === fv.days ? 'bg-lavender-700 text-white' : 'text-slate-600 hover:bg-lavender-50'}" aria-pressed="${d === fv.days}">${l}</button>`).join('')}
          </div>
          <label class="flex items-center gap-1.5 rounded-full border border-lavender-200 bg-white/60 pl-3 pr-1 py-0.5 text-xs text-slate-600">Daily goal
            <input data-fv-goal type="number" min="5" max="960" step="5" value="${st.goal_min}" class="w-16 rounded-full border border-lavender-200 bg-white px-2 py-0.5 text-xs text-right focus:outline-none focus:ring-2 focus:ring-lavender-300" aria-label="Daily focus goal in minutes"> min</label>
          <button data-fv-ask class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-bold px-3 py-1.5">✦ Ask my agent</button>
        </div>
      </div>

      ${nowBanner(st)}

      <div class="shrink-0 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        ${tile('Today', fmtMin(t.focus_min), `of ${fmtMin(st.goal_min)} goal`, Math.min(1, t.focus_min / st.goal_min))}
        ${tile('Score today', t.score ?? '–', t.score == null ? 'no sessions yet' : 'out of 100', null, 'Score: 70% how much of your daily goal you reached, 30% how productive your sessions felt (your 1–5 ratings; the share you finished when you didn’t rate).')}
        ${tile('Sessions today', t.sessions, `${t.completed} finished`)}
        ${tile('Streak', `${st.streak} ${st.streak === 1 ? 'day' : 'days'}`, 'in a row with a finished session')}
        ${tile(`Last ${st.days} days`, fmtMin(tot.focus_min), `${fmtMin(tot.focus_min / st.days)} a day · ${tot.active_days} active day${tot.active_days === 1 ? '' : 's'}`)}
        ${tile('Productivity', tot.avg_rating ? `${tot.avg_rating.toFixed(1)}/5` : '–', tot.completion_rate == null ? 'rate sessions after the timer' : `${Math.round(tot.completion_rate * 100)}% of sessions finished`)}
      </div>

      <div class="grid grid-cols-1 gap-3 lg:grid-cols-12 lg:grid-rows-2 lg:flex-1 lg:min-h-0">
        ${panel('Focus per day', `${legendHTML(st)}<div data-fv-daily class="relative flex-1 min-h-[12rem] lg:min-h-0"></div>`, { cls: 'lg:col-span-8 min-h-0' })}
        ${panel('By project', projectsHTML(st), { cls: 'lg:col-span-4 min-h-0' })}
        ${panel('When you focus', hoursHTML(st), { cls: 'lg:col-span-4 min-h-0' })}
        ${panel(`Sessions (${st.sessions.length})`, logHTML(st), { cls: 'lg:col-span-8 min-h-0' })}
      </div>
      <div data-fv-tip class="hidden fixed z-[70] pointer-events-none max-w-[16rem] rounded-xl border border-lavender-200 bg-white px-3 py-2 text-xs text-slate-700 shadow-lg"></div>
    </div>`;

  const entering = enterPage('focus', 0);
  if (entering) stagger(app.querySelectorAll('.hud-in'), 'pop-in', 40);
  const chart = app.querySelector('[data-fv-daily]');
  const draw = () => drawDaily(chart, st);
  requestAnimationFrame(draw);
  fv.ro?.disconnect();
  fv.ro = new ResizeObserver(draw);
  fv.ro.observe(chart);
  bindFocus();
}

function tile(label, value, sub, progress = null, help = '') {
  return `<div class="hud-panel hud-in !p-3" ${help ? `title="${esc(help)}"` : ''}>
    <p class="hud-label !pl-1">${esc(label)}${help ? ' ⓘ' : ''}</p>
    <p class="hud-num mt-1.5 text-2xl font-extrabold text-slate-900 leading-none">${esc(String(value))}</p>
    <p class="mt-1 text-[11px] text-slate-500 truncate">${esc(sub)}</p>
    ${progress != null ? `<div class="mt-1.5 h-1.5 rounded-full bg-slate-200/60 overflow-hidden"><div class="h-1.5 rounded-full" style="width:${Math.round(progress * 100)}%;background:var(--series-1)"></div></div>` : ''}
  </div>`;
}

// What's running now, or the session waiting for a rating (the same prompt as on the HUD timer).
function nowBanner(st) {
  const t = hud.timer;
  if (t.rateId) return `<div class="shrink-0 flex flex-wrap items-center gap-2 rounded-2xl border border-butter-200 bg-butter-50 px-4 py-2 text-sm">
      <span class="font-bold text-butter-700">How productive was your last session?</span>
      ${[1, 2, 3, 4, 5].map(n => `<button data-fv-rate="${n}" class="w-7 h-7 rounded-lg bg-white border border-butter-200 font-bold text-slate-700 hover:bg-butter-100">${n}</button>`).join('')}
      <button data-fv-rate="0" class="ml-auto text-xs text-slate-400 hover:text-slate-700">Skip</button></div>`;
  const cur = st.current;
  if (!cur && !t.sessionId) return '';
  return `<a href="#/" class="shrink-0 flex items-center gap-3 rounded-2xl border border-lilac-200 bg-lilac-50 px-4 py-2 text-sm hover:shadow">
    <span class="hud-dot bg-lilac-300"></span>
    <span class="min-w-0 flex-1 truncate">Focusing on <b class="text-slate-800">${esc(cur?.title || t.task?.title || '')}</b>${cur?.project_title && cur.project_title !== cur.title ? ` · ${esc(cur.project_title)}` : ''}</span>
    <span class="hud-num font-bold text-lilac-700" data-fv-left>${focusClockText()}</span></a>`;
}

function legendHTML(st) {
  const keys = new Set(st.by_day.flatMap(d => Object.keys(d.by_project)));
  const list = [...keys].sort((a, b) => fv.series.order(a) - fv.series.order(b));
  if (list.length < 2) return '<div class="shrink-0 -mt-1 mb-1 h-4"></div>';
  return `<div class="shrink-0 -mt-1 mb-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-600">
    ${list.map(k => `<span class="flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-sm" style="background:${fv.series.color(k)}"></span>${esc(fv.series.name(k))}</span>`).join('')}</div>`;
}

// Stacked bars per day (by project) with the daily goal as a dashed line. Drawn to the panel's size.
function drawDaily(el, st) {
  const W = el.clientWidth, H = el.clientHeight;
  if (!W || !H) return;
  const m = { l: 38, r: 8, t: 8, b: 20 }, pw = W - m.l - m.r, ph = H - m.t - m.b;
  const max = Math.max(st.goal_min, ...st.by_day.map(d => d.focus_min));
  const step = [15, 30, 60, 120, 180, 240].find(s => max / s <= 4) || 360;
  const top = Math.ceil((max * 1.08) / step) * step;
  const y = v => m.t + ph - (v / top) * ph;
  const n = st.by_day.length, band = pw / n, bw = Math.max(2, Math.min(28, band * (n > 40 ? 0.7 : 0.58)));
  const ticks = Array.from({ length: Math.floor(top / step) + 1 }, (_, i) => i * step);
  const labelEvery = n <= 10 ? 1 : n <= 31 ? 7 : 14;
  const today = st.to;
  let svg = ticks.map(v => `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="rgb(var(--c-slate-200))" stroke-width="1" ${v ? 'stroke-dasharray="2 3"' : ''}/>
    <text x="${m.l - 6}" y="${y(v) + 3.5}" text-anchor="end" font-size="10" fill="rgb(var(--c-slate-400))">${v ? fmtMin(v, true) : '0'}</text>`).join('');
  st.by_day.forEach((d, i) => {
    const x = m.l + i * band + (band - bw) / 2;
    const segs = Object.entries(d.by_project).filter(([, v]) => v > 0).sort((a, b) => fv.series.order(a[0]) - fv.series.order(b[0]));
    let base = 0;
    segs.forEach(([k, v], j) => {
      const y0 = y(base), y1 = y(base + v), last = j === segs.length - 1;
      const h = Math.max(1, y0 - y1 - (j ? 2 : 0));   // 2px surface gap between stacked segments
      const yy = y0 - (j ? 2 : 0) - h;
      svg += last ? `<path d="${topRounded(x, yy, bw, h, Math.min(4, bw / 2, h))}" fill="${fv.series.color(k)}"/>` : `<rect x="${x}" y="${yy}" width="${bw}" height="${h}" fill="${fv.series.color(k)}"/>`;
      base += v;
    });
    const date = new Date(d.date + 'T00:00');
    if (i % labelEvery === 0 || d.date === today) {
      const label = n <= 10 ? date.toLocaleDateString([], { weekday: 'short' }) : date.toLocaleDateString([], { day: 'numeric', month: 'short' });
      svg += `<text x="${m.l + i * band + band / 2}" y="${H - 5}" text-anchor="middle" font-size="10" font-weight="${d.date === today ? 800 : 400}" fill="rgb(var(--c-slate-${d.date === today ? 700 : 400}))">${esc(label)}</text>`;
    }
    const tip = `<b>${esc(date.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' }))}</b><br>${fmtMin(d.focus_min)} in ${d.sessions} session${d.sessions === 1 ? '' : 's'}${d.score != null ? ` · score ${d.score}` : ''}`
      + segs.map(([k, v]) => `<br><span style="color:${fv.series.color(k)}">■</span> ${esc(fv.series.name(k))}: ${fmtMin(v)}`).join('');
    svg += `<rect x="${m.l + i * band}" y="${m.t}" width="${band}" height="${ph}" fill="transparent" data-tip="${esc(tip)}"/>`;
  });
  const gy = y(st.goal_min);
  svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${gy}" y2="${gy}" stroke="rgb(var(--c-slate-500))" stroke-width="1.5" stroke-dasharray="5 4"/>
    <text x="${W - m.r}" y="${gy - 4}" text-anchor="end" font-size="10" font-weight="700" fill="rgb(var(--c-slate-500))">Goal ${fmtMin(st.goal_min, true)}</text>`;
  const empty = !st.by_day.some(d => d.sessions);
  el.innerHTML = `<svg width="${W}" height="${H}" role="img" aria-label="Focus minutes per day for the last ${n} days">${svg}</svg>
    ${empty ? '<p class="absolute inset-0 flex items-center justify-center text-sm text-slate-400">No focus sessions yet. Start the timer on the HUD.</p>' : ''}`;
}
function topRounded(x, y, w, h, r) {
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

function projectsHTML(st) {
  if (!st.projects.length) return '<p class="text-sm text-slate-400">Nothing yet.</p>';
  const max = Math.max(...st.projects.map(p => p.focus_min)), total = st.total.focus_min || 1;
  return `<div class="hud-scroll scroll-thin -mr-1 pr-1">
    <ul class="space-y-2">
      ${st.projects.map(p => `<li data-tip="${esc(`<b>${esc(p.title)}</b><br>${fmtMin(p.focus_min)} in ${p.sessions} session${p.sessions === 1 ? '' : 's'}${p.avg_rating ? `<br>Productivity ${p.avg_rating}/5` : ''}`)}">
        <div class="flex items-baseline justify-between gap-2 text-xs">
          ${p.id ? `<a href="#/b/${p.id}" class="font-semibold text-slate-700 truncate hover:underline">${esc(p.title)}</a>` : `<span class="font-semibold text-slate-700 truncate">${esc(p.key === 'other' ? 'Other (no project)' : `${p.title} (deleted)`)}</span>`}
          <span class="hud-num shrink-0 text-slate-500">${fmtMin(p.focus_min)} · ${Math.round(p.focus_min / total * 100)}%</span>
        </div>
        <div class="mt-1 h-2.5 rounded-r-[4px]" style="width:${Math.max(2, p.focus_min / max * 100)}%;background:${fv.series.color(p.key)}"></div>
      </li>`).join('')}
    </ul>
    ${st.tasks.length ? `<p class="hud-label !pl-0 mt-4 mb-1.5">Top tasks</p>
    <ol class="space-y-1 text-xs">${st.tasks.slice(0, 6).map(t => `<li class="flex items-center justify-between gap-2">
      ${t.block_id ? `<a href="#/b/${t.block_id}" class="truncate text-slate-700 hover:underline">${esc(t.title)}</a>` : `<span class="truncate text-slate-700">${esc(t.title)}</span>`}
      <span class="hud-num shrink-0 text-slate-500">${fmtMin(t.focus_min)}</span></li>`).join('')}</ol>` : ''}
  </div>`;
}

// 24 hourly cells, one blue that darkens with more focus time (sequential: one hue, light to dark).
function hoursHTML(st) {
  const max = Math.max(...st.hours.map(h => h.focus_min));
  const cell = h => {
    const step = h.focus_min ? Math.max(1, Math.ceil(h.focus_min / max * 6)) : 0;
    const tip = `<b>${String(h.hour).padStart(2, '0')}:00–${String((h.hour + 1) % 24).padStart(2, '0')}:00</b><br>${h.focus_min ? `${fmtMin(h.focus_min)} started in this hour` : 'No sessions'}${h.avg_rating ? `<br>Productivity ${h.avg_rating}/5` : ''}`;
    return `<div class="h-full min-h-[1.75rem] rounded-[3px] ${step ? '' : 'bg-slate-100'}" style="${step ? `background:var(--seq-${step})` : ''}" data-tip="${esc(tip)}"></div>`;
  };
  const best = st.hours.filter(h => h.avg_rating).sort((a, b) => b.avg_rating - a.avg_rating || b.focus_min - a.focus_min)[0];
  return `<div class="flex-1 min-h-0 flex flex-col justify-center gap-1.5">
    <div class="grid gap-[2px] flex-1 max-h-24" style="grid-template-columns:repeat(24,minmax(0,1fr))">${st.hours.map(cell).join('')}</div>
    <div class="grid text-[10px] text-slate-400" style="grid-template-columns:repeat(4,minmax(0,1fr))"><span>00</span><span>06</span><span>12</span><span>18</span></div>
    <div class="flex items-center gap-1.5 text-[10px] text-slate-400">Less ${[1, 2, 3, 4, 5, 6].map(i => `<span class="w-3 h-2.5 rounded-[2px]" style="background:var(--seq-${i})"></span>`).join('')} More</div>
  </div>
  <p class="shrink-0 mt-2 text-xs text-slate-500">${st.best_hour != null ? `You focus most around <b class="text-slate-700">${String(st.best_hour).padStart(2, '0')}:00</b>.` : 'Patterns show up after a few sessions.'}${best ? ` Your best-rated hour: <b class="text-slate-700">${String(best.hour).padStart(2, '0')}:00</b> (${best.avg_rating}/5).` : ''}</p>`;
}

function logHTML(st) {
  if (!st.sessions.length) return '<p class="text-sm text-slate-400">Your sessions appear here once you use the timer: pick what you’re working on, then press Start.</p>';
  const row = s => {
    const d = new Date(s.started_at);
    const key = s.project_id ? String(s.project_id) : s.project_title ? `t:${s.project_title}` : 'other';
    const result = s.status === 'completed' ? '<span class="text-mint-700" title="Ran to the end">✓ Finished</span>'
      : s.status === 'running' ? '<span class="text-lilac-700">● Running</span>' : '<span class="text-slate-400" title="Stopped before the end">◐ Stopped</span>';
    return `<tr class="border-t border-slate-100 align-middle" data-fv-sid="${s.id}">
      <td class="py-1.5 pr-2 whitespace-nowrap text-slate-500 hud-num">${d.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${d.toTimeString().slice(0, 5)}</td>
      <td class="py-1.5 pr-2 max-w-[14rem]"><div class="truncate font-semibold text-slate-800">${s.block_id ? `<a href="#/b/${s.block_id}" class="hover:underline">${esc(s.title)}</a>` : esc(s.title)}</div>
        <div class="flex items-center gap-1 text-[11px] text-slate-400 truncate"><span class="w-2 h-2 shrink-0 rounded-sm" style="background:${fv.series.color(key)}"></span>${esc(s.project_title || 'No project')}</div></td>
      <td class="py-1.5 pr-2 whitespace-nowrap hud-num text-slate-700">${fmtMin(s.focus_min)}<span class="text-slate-400">${s.mode === 'stopwatch' ? ' · stopwatch' : ` / ${Math.round(s.planned_min)}`}</span></td>
      <td class="py-1.5 pr-2 whitespace-nowrap text-[11px] font-semibold">${result}</td>
      <td class="py-1.5 pr-2 whitespace-nowrap" aria-label="Productivity">${[1, 2, 3, 4, 5].map(n => `<button data-fv-star="${n}" class="text-sm leading-none ${s.rating >= n ? 'text-butter-300' : 'text-slate-300 hover:text-butter-200'}" aria-label="Rate ${n} of 5" title="${['', 'Barely', 'A bit', 'OK', 'Good', 'Great'][n]}">★</button>`).join('')}</td>
      <td class="py-1.5 pr-2 w-full min-w-[8rem]"><input data-fv-note value="${esc(s.note)}" maxlength="1000" placeholder="Add a note…" class="w-full rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-xs text-slate-600 hover:border-slate-200 focus:border-lavender-300 focus:bg-white focus:outline-none"></td>
      <td class="py-1.5"><button data-fv-del class="p-1 rounded text-slate-300 hover:text-blush-700 hover:bg-blush-50" aria-label="Delete this session">✕</button></td>
    </tr>`;
  };
  return `<div class="hud-scroll scroll-thin overflow-x-auto">
    <table class="w-full text-xs">
      <thead class="sticky top-0 bg-white/95 backdrop-blur text-[10px] uppercase tracking-wider text-slate-400 text-left">
        <tr><th class="font-bold py-1 pr-2">When</th><th class="font-bold py-1 pr-2">What</th><th class="font-bold py-1 pr-2">Focused</th><th class="font-bold py-1 pr-2">Result</th><th class="font-bold py-1 pr-2">Productivity</th><th class="font-bold py-1 pr-2">Note</th><th></th></tr>
      </thead>
      <tbody>${st.sessions.map(row).join('')}</tbody>
    </table></div>`;
}

function bindFocus() {
  const root = app.querySelector('.fv'), tip = root.querySelector('[data-fv-tip]');
  root.querySelectorAll('[data-fv-days]').forEach(b => b.onclick = () => {
    fv.days = Number(b.dataset.fvDays);
    localSet('nestwork.focusDays', String(fv.days));
    renderFocus();
  });
  root.querySelector('[data-fv-goal]').onchange = async e => {
    const minutes = Math.round(Number(e.target.value));
    if (!(minutes >= 5 && minutes <= 960)) { e.target.value = fv.stats.goal_min; return toast('The goal can be 5 to 960 minutes.', 'error'); }
    if (await attempt(() => api('PUT', '/api/focus/goal', { minutes }))) renderFocus();
  };
  root.querySelector('[data-fv-ask]').onclick = () => {
    setChatOpen(true, { focus: false });
    sendMessage(`Look at my focus stats for the last ${fv.days} days. How productive have I been, what did my time go to, and what's one thing I should change?`);
  };
  root.querySelectorAll('[data-fv-rate]').forEach(b => b.onclick = () => rateSession(Number(b.dataset.fvRate) || null));
  root.querySelectorAll('[data-fv-star]').forEach(b => b.onclick = async () => {
    const sid = Number(b.closest('[data-fv-sid]').dataset.fvSid), n = Number(b.dataset.fvStar);
    const cur = fv.stats.sessions.find(s => s.id === sid)?.rating;
    if (await attempt(() => api('PATCH', `/api/focus/${sid}`, { rating: cur === n ? 0 : n }))) renderFocus();   // same star again clears
  });
  root.querySelectorAll('[data-fv-note]').forEach(inp => {
    inp.onchange = () => attempt(() => api('PATCH', `/api/focus/${Number(inp.closest('[data-fv-sid]').dataset.fvSid)}`, { note: inp.value }));
    inp.onkeydown = e => { if (e.key === 'Enter') inp.blur(); };
  });
  root.querySelectorAll('[data-fv-del]').forEach(b => b.onclick = async () => {
    const sid = Number(b.closest('[data-fv-sid]').dataset.fvSid), s = fv.stats.sessions.find(x => x.id === sid);
    if (!confirm(`Delete the session “${s.title}” (${fmtMin(s.focus_min)})? It won’t count in your stats any more.`)) return;
    if (await attempt(() => api('DELETE', `/api/focus/${sid}`))) {
      if (hud.timer.sessionId === sid) { hud.timer.sessionId = null; saveTimer(); }
      renderFocus();
    }
  });
  // One tooltip for every chart mark, project bar and hour cell.
  root.addEventListener('pointermove', e => {
    const el = e.target.closest('[data-tip]');
    if (!el) { tip.classList.add('hidden'); return; }
    tip.innerHTML = el.dataset.tip;
    tip.classList.remove('hidden');
    const r = tip.getBoundingClientRect();
    tip.style.left = `${Math.min(innerWidth - r.width - 8, e.clientX + 14)}px`;
    tip.style.top = `${Math.max(8, e.clientY - r.height - 10)}px`;
  });
  root.addEventListener('pointerleave', () => tip.classList.add('hidden'));
}

// "12:34 left" for a pomodoro, "1:02:03" for a running stopwatch, or "paused".
function focusClockText() {
  const t = hud.timer;
  if (isStopwatch()) return t.swFrom ? fmtElapsed(swMs()) : 'paused';
  return t.endsAt ? `${fmtClock(timerLeft())} left` : 'paused';
}

setInterval(() => {   // the "12:34 left" of a running session
  const el = location.hash.startsWith('#/focus') && app.querySelector('[data-fv-left]');
  if (el) el.textContent = focusClockText();
}, 1000);
