// The Settings panel: agent, memory files, AI access and proposals, focus timer, appearance, help,
// connected apps, trash & backups, and the danger zone. Uses app.js and editor.js (openModal).

// ---------------------------------------------------------------- Settings → ✦ Agent → Connected apps
// The claude.ai connectors (Google Calendar, Gmail, …) the agent may use in the chat. Off by default; see
// nestwork/connectors.py. "Check" asks Claude Code which apps it has without a model call.
async function connectedApps(box, check = false) {
  let d;
  try { d = check ? await api('POST', '/api/connectors/check') : await api('GET', '/api/connectors'); }
  catch (e) { if (check) toast(e.message, 'error'); d = d || { servers: [], checked_at: null }; }
  const apps = d.servers.filter(x => x.claude_ai);
  const STATUS = {
    connected: ['bg-mint-50 text-mint-700', 'Ready'], 'needs-auth': ['bg-butter-50 text-butter-700', 'Needs sign-in'],
    pending: ['bg-slate-100 text-slate-500', 'Starting… check again'], failed: ['bg-blush-50 text-blush-700', 'Not working'],
    missing: ['bg-blush-50 text-blush-700', 'Not found'],
  };
  const needAuth = apps.filter(a => a.status === 'needs-auth');
  const ready = apps.filter(a => a.status === 'connected');
  const on = apps.filter(a => a.enabled);
  // Where the user is: 1 connect on claude.ai, 2 sign in for Claude Code, 3 turn on, 4 use it.
  const step = !apps.length ? 1 : needAuth.some(a => a.enabled) || (!ready.length && needAuth.length) ? 2 : !on.length ? 3 : 4;
  const when = d.checked_at ? new Date(d.checked_at * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : null;
  const btn = 'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold';
  const stepBox = (n, title, body) => `
    <li class="flex gap-3 rounded-xl border ${n === step ? 'border-lavender-300 bg-lavender-50/60' : 'border-slate-200'} p-3">
      <span class="shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${n < step ? 'bg-mint-100 text-mint-700' : n === step ? 'bg-lavender-700 text-white' : 'bg-slate-100 text-slate-500'}">${n < step ? '✓' : n}</span>
      <div class="min-w-0 flex-1"><p class="text-sm font-bold text-slate-700">${title}</p><div class="mt-1 space-y-1.5 text-xs text-slate-600">${body}</div></div>
    </li>`;
  box.innerHTML = `
    <h3 class="text-sm font-bold text-slate-700">🔌 Connected apps</h3>
    <p class="text-xs text-slate-500">Let your agent use Google Calendar, Gmail, Google Drive and other apps, e.g. “put my exam on my calendar” or “any emails from my professor?”. It takes a few minutes once; follow the steps.</p>
    <ol class="mt-3 space-y-2">
      ${stepBox(1, 'Connect the app to your Claude account', `
        <p>On claude.ai, open Connectors, find the app (for example <b>Google Calendar</b>), click <b>Connect</b> and sign in with your Google account. Allow the access it asks for.</p>
        <p class="flex flex-wrap items-center gap-2"><a href="https://claude.ai/settings/connectors" target="_blank" rel="noopener" class="${btn} bg-lavender-700 text-white hover:bg-lavender-800">Open claude.ai Connectors ↗</a>
          <span class="text-slate-400">Already connected there? Go to step 2.</span></p>`)}
      ${stepBox(2, 'Check that Claude Code can use it', `
        <p>Click <b>${apps.length ? 'Check again' : 'Find my apps'}</b>: Nestwork asks Claude Code which apps you have (a few seconds, no AI cost). Apps marked <b>Ready</b> need nothing more.</p>
        <p>An app marked <b>Needs sign-in</b> has to be signed in once for Claude Code too: click <b>Sign in</b> next to it. ${d.can_open_sign_in
          ? 'A Terminal window opens with Claude Code’s app list. Pick the app (e.g. “claude.ai Gmail”), choose <b>Authenticate</b> and sign in with Google in the browser page that opens. Then type <code>/exit</code>, close the window and click <b>Check again</b> here.'
          : `Open a terminal, run <code>${esc(d.sign_in_command)}</code>, pick the app, choose <b>Authenticate</b> and sign in with Google in the browser. Then type <code>/exit</code> and click <b>Check again</b> here.`}</p>
        <p class="text-slate-400">If Claude Code asks you a question first, answer it. If you see Claude’s prompt instead of the app list, type <code>/mcp</code> and press Enter.</p>
        <p class="flex flex-wrap items-center gap-2"><button data-apps-check class="${btn} border border-lavender-300 text-lavender-700 hover:bg-lavender-50">${apps.length ? 'Check again' : 'Find my apps'}</button>
          ${when ? `<span class="text-slate-400">Last checked ${esc(when)}</span>` : ''}</p>`)}
      ${stepBox(3, 'Turn it on for your agent', `
        ${apps.length ? `<ul class="space-y-1.5">${apps.map(a => { const [cls, txt] = STATUS[a.status] || STATUS.pending; return `
          <li class="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2">
            <input type="checkbox" data-app="${esc(a.name)}" ${a.enabled ? 'checked' : ''} aria-label="Let the agent use ${esc(a.label)}">
            <span class="flex-1 min-w-0 text-sm font-semibold text-slate-700 truncate">${esc(a.label)}</span>
            <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold ${cls}">${txt}</span>
            ${a.status === 'needs-auth' ? `<button data-apps-signin class="${btn} bg-butter-300/20 border border-butter-300 text-butter-700 hover:bg-butter-50">Sign in</button>` : ''}
          </li>`; }).join('')}</ul>
          <p>Tick the apps your agent may use. It can from your next chat message.${needAuth.some(a => a.enabled) ? ' <span class="text-butter-700">An app that still needs sign-in won’t work until step 2 is done.</span>' : ''}</p>`
          : '<p class="text-slate-400">Your apps show up here after step 2.</p>'}`)}
      ${stepBox(4, 'Ask your agent', `
        <p>Open the chat and ask, for example:</p>
        <ul class="list-disc pl-5 space-y-0.5 text-slate-500">
          <li>“Put ‘Thesis draft due’ on my calendar for Friday at 10:00.”</li>
          <li>“Add every to-do that’s due this week to my calendar.”</li>
          <li>“What’s on my calendar tomorrow morning?”</li>
          <li>“Any emails from my professor this week? Add a to-do for anything I need to answer.”</li>
        </ul>`)}
    </ol>
    <p class="mt-3 text-xs text-slate-500">Good to know: the agent only uses the apps you tick here, plus Nestwork. It treats emails and events as information, never as instructions, and won’t send or delete mail, invite people or delete events unless you ask it to in the chat. Changes to your projects still follow 🤖 AI access. Calendar events get your calendar’s own reminders, so they reach your phone too.</p>`;
  box.querySelector('[data-apps-check]').onclick = e => { e.target.disabled = true; e.target.textContent = 'Checking…'; connectedApps(box, true); };
  box.querySelectorAll('[data-apps-signin]').forEach(b => b.onclick = async () => {
    const r = await attempt(() => api('POST', '/api/connectors/sign-in'));
    if (!r) return;
    if (r.opened) toast('A Terminal window opened with Claude Code’s app list. Pick the app, choose Authenticate and sign in, then click Check again here.', 'success');
    else { try { await navigator.clipboard.writeText(r.command); } catch { /* no clipboard */ } toast(`Run “${r.command}” in a terminal (copied), pick the app, choose Authenticate and sign in.`, 'info'); }
  });
  box.querySelectorAll('[data-app]').forEach(cb => cb.onchange = async () => {
    const names = [...box.querySelectorAll('[data-app]:checked')].map(x => x.dataset.app);
    if (!await attempt(() => api('PUT', '/api/connectors', { enabled: names }))) { cb.checked = !cb.checked; return; }
    toast(cb.checked ? `Your agent can use ${cb.closest('li').querySelector('span').textContent} from your next message.` : 'Turned off.', 'success');
    connectedApps(box);
  });
}

// ---------------------------------------------------------------- Settings → 🗑 Trash & backups
// Deleted blocks wait in the trash for 30 days (restore puts them back where they were, with their rules,
// notes and reminders). Backups: daily automatic copies of the database, plus full ones made by you or
// before a reset; restoring one replaces everything (and backs up the current state first).
async function trashPane(pane) {
  let t, s;
  try { [t, s] = await Promise.all([api('GET', '/api/trash'), api('GET', '/api/backups')]); }
  catch (e) { pane.innerHTML = `<p class="text-sm text-blush-700">${esc(e.message)}</p>`; return; }
  const when = iso => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  const size = n => n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
  pane.innerHTML = `
    <section class="rounded-2xl border border-slate-200 p-4">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div><h3 class="text-sm font-bold text-slate-700">🗑 Trash</h3>
          <p class="text-xs text-slate-500">Deleted projects and to-dos stay here for ${t.keep_days} days, with everything inside them, their rules, notes and reminders. Deletes by your agent land here too.</p></div>
        ${t.items.length ? '<button data-tr-empty class="shrink-0 rounded-full border border-blush-200 px-3 py-1 text-xs font-semibold text-blush-700 hover:bg-blush-50">Empty trash…</button>' : ''}
      </div>
      ${t.items.length ? `<ul class="mt-3 space-y-2">${t.items.map(i => `
        <li class="rounded-xl border border-slate-200 p-2.5 flex flex-wrap items-center justify-between gap-2" data-tr="${i.id}">
          <div class="min-w-0">
            <p class="text-sm font-semibold text-slate-700 truncate">${i.is_project ? '📁 ' : ''}${esc(i.title)}${i.items ? ` <span class="font-normal text-slate-400">+ ${i.items} inside</span>` : ''}</p>
            <p class="text-xs text-slate-500">${i.is_project ? 'Project' : `In ${esc(i.path)}`} · deleted ${esc(when(i.deleted_at))}${i.deleted_by === 'agent' ? ' by your agent' : ''} · gone for good on ${esc(fmtDay(i.purge_on))}</p>
          </div>
          <div class="shrink-0 flex gap-1.5">
            <button data-tr-restore class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white px-3 py-1 text-xs font-semibold">Restore</button>
            <button data-tr-forget class="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-500 hover:bg-slate-50" title="Delete it for good">✕</button>
          </div>
        </li>`).join('')}</ul>` : '<p class="mt-2 text-xs text-slate-400">The trash is empty.</p>'}
    </section>

    <section class="mt-4 rounded-2xl border border-slate-200 p-4">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div><h3 class="text-sm font-bold text-slate-700">🗄 Backups</h3>
          <p class="text-xs text-slate-500">Nestwork saves the database (and the shared memory files) once a day while it runs, keeping the last ${s.keep_auto} days. Full backups, with your agent’s photos and pictures, are made before every reset and restore, or when you click <i>Back up now</i>; every backup, of any kind, is deleted automatically once it is ${s.max_age_days} days old. Restoring replaces what you have now with the backup (and backs up the current state first).</p></div>
        <div class="shrink-0 flex gap-1.5">
          <a href="/api/export.json" download class="inline-flex items-center gap-1 rounded-full border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50" title="Every project (archived ones too) as one JSON file you can import elsewhere">${ico('download')}Export</a>
          <button data-bk-now class="rounded-full border border-lavender-300 px-3 py-1 text-xs font-semibold text-lavender-700 hover:bg-lavender-50">Back up now</button>
        </div>
      </div>
      ${s.backups.length ? `<ul class="mt-3 space-y-2">${s.backups.map(b => `
        <li class="rounded-xl border border-slate-200 p-2.5" data-dz-backup="${esc(b.name)}">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="min-w-0">
              <p class="text-sm font-semibold text-slate-700">${esc(when(b.created_at))} <span class="font-normal text-slate-400">· ${esc(b.reason)}${b.auto ? ' (database only)' : ''}</span></p>
              <p class="text-xs text-slate-500">${b.projects} project${b.projects === 1 ? '' : 's'}, ${b.todos} to-dos, ${b.focus_sessions} focus sessions, ${b.chat_messages} messages${b.agent_name ? `, agent “${esc(b.agent_name)}”` : ''}${b.size ? ` · ${size(b.size)}` : ''}</p>
            </div>
            <button data-dz-restore class="shrink-0 rounded-full border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50">Restore…</button>
          </div>
        </li>`).join('')}</ul>` : '<p class="mt-2 text-xs text-slate-400">No backups yet. The first daily one is made once there’s something to keep.</p>'}
    </section>`;

  pane.querySelectorAll('[data-tr-restore]').forEach(btn => btn.onclick = async () => {
    const id = btn.closest('[data-tr]').dataset.tr;
    btn.disabled = true;
    if (await attempt(() => api('POST', `/api/trash/${id}/restore`))) { trashPane(pane); renderCurrent(); }
    else btn.disabled = false;
  });
  pane.querySelectorAll('[data-tr-forget]').forEach(btn => btn.onclick = async () => {
    const li = btn.closest('[data-tr]');
    if (!confirm('Delete this for good? It can’t be brought back after this (except from a backup).')) return;
    if (await attempt(() => api('DELETE', `/api/trash/${li.dataset.tr}`))) trashPane(pane);
  });
  pane.querySelector('[data-tr-empty]')?.addEventListener('click', async () => {
    if (!confirm(`Delete all ${t.items.length} item(s) in the trash for good? They can’t be brought back after this (except from a backup).`)) return;
    if (await attempt(() => api('DELETE', '/api/trash'))) trashPane(pane);
  });
  pane.querySelector('[data-bk-now]').onclick = async e => {
    e.target.disabled = true; e.target.textContent = 'Backing up…';
    const r = await attempt(() => api('POST', '/api/backups'));
    if (r) { toast('Backup saved ✓', 'success'); trashPane(pane); } else { e.target.disabled = false; e.target.textContent = 'Back up now'; }
  };
  pane.querySelectorAll('[data-dz-restore]').forEach(btn => btn.onclick = () => {
    const li = btn.closest('[data-dz-backup]');
    if (li.querySelector('[data-dz-restore-form]')) return;
    li.insertAdjacentHTML('beforeend', `<div data-dz-restore-form class="mt-2 flex flex-wrap items-center gap-2">
      <span class="text-xs text-slate-600">Type <b class="font-mono">restore</b>:</span>
      <input autocomplete="off" spellcheck="false" class="w-32 rounded-lg border border-slate-300 px-2 py-1 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-lavender-300" aria-label="Type restore">
      <button disabled class="rounded-full bg-lavender-700 text-white text-xs font-bold px-3 py-1 disabled:opacity-40">Replace everything with this backup</button></div>`);
    const form = li.querySelector('[data-dz-restore-form]'), inp = form.querySelector('input'), ok = form.querySelector('button');
    inp.focus();
    inp.oninput = () => { ok.disabled = inp.value.trim().toLowerCase() !== 'restore'; };
    ok.onclick = async () => {
      ok.disabled = true;
      ok.textContent = 'Restoring…';
      if (await attempt(() => api('POST', '/api/reset/restore', { name: li.dataset.dzBackup, confirm: inp.value }))) {
        try { localStorage.removeItem('nestwork.hudTimer'); } catch { /* private mode */ }
        sessionStorage.setItem('nestwork.afterRestore', li.dataset.dzBackup);
        location.hash = '#/';
        location.reload();
      }
    };
  });
}

// ---------------------------------------------------------------- Settings → ⚠️ Danger zone
// Start over fresh. Safety: you pick exactly what goes (the shared memory is never picked for you), see it
// in numbers, type a phrase, and a backup is saved first so it can be restored (🗑 Trash & backups). No agent tool does this.
const DANGER_PARTS = [
  ['projects', '📁 Projects & to-dos', s => `${s.projects} project${s.projects === 1 ? '' : 's'}, ${s.todos} to-do${s.todos === 1 ? '' : 's'}, ${s.notes} note${s.notes === 1 ? '' : 's'}, ${s.reminders} pending reminder${s.reminders === 1 ? '' : 's'}${s.trash ? `, ${s.trash} item${s.trash === 1 ? '' : 's'} in the trash` : ''}`, true],
  ['focus', '📈 Focus history', s => `${s.focus_sessions} focus session${s.focus_sessions === 1 ? '' : 's'} and their ratings`, true],
  ['chat', '💬 Chat history', s => `${s.chat_messages} message${s.chat_messages === 1 ? '' : 's'} with your agent`, true],
  ['agent', '✦ Your agent', s => `${s.agent_name ? `“${s.agent_name}”: ` : ''}name, personality, ${s.photos} photo${s.photos === 1 ? '' : 's'}, ${s.wardrobe} wardrobe item${s.wardrobe === 1 ? '' : 's'}, ${s.pictures} picture${s.pictures === 1 ? '' : 's'}. Erasing the agent erases everything above and below it too: you start again from the awakening, with no projects.`, true],
  ['settings', '⚙ Settings', () => 'AI access, models, pictures, reminder style, and this browser’s timer and view preferences go back to the defaults', true],
  ['shared', '🧠 Shared memory', s => `“About me”${s.user_name ? ` (${s.user_name})` : ''} and memory.md in ${s.shared_dir}. Every Claude session on this computer uses these, not just Nestwork.`, false],
];

async function dangerZone(pane) {
  let s;
  try { s = await api('GET', '/api/reset'); } catch (e) { pane.innerHTML = `<p class="text-sm text-blush-700">${esc(e.message)}</p>`; return; }
  const when = iso => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  pane.innerHTML = `
    <section class="rounded-2xl border-2 border-blush-300 bg-blush-50/60 p-4">
      <h3 class="text-base font-extrabold text-blush-700">Start over</h3>
      <p class="text-sm text-slate-600 mt-1">Erase what you pick below to begin fresh. Nestwork saves a full backup first (in <code class="text-xs">data/backups/</code>), so you can bring everything back from 🗑 Trash &amp; backups.</p>
      <div class="mt-3 space-y-2">
        ${DANGER_PARTS.map(([k, label, detail, on]) => `
          <label class="flex items-start gap-3 rounded-xl border ${k === 'shared' ? 'border-butter-300 bg-butter-50' : 'border-slate-200 bg-white'} p-2.5 cursor-pointer">
            <input type="checkbox" data-dz-part="${k}" ${on ? 'checked' : ''} class="mt-0.5 rounded text-blush-700">
            <span><span class="block text-sm font-semibold text-slate-800">${label}${k === 'shared' ? ' <span class="ml-1 rounded-full bg-butter-200 px-2 py-0.5 text-[10px] font-bold text-butter-700">off unless you tick it</span>' : ''}</span>
            <span class="block text-xs text-slate-500">${esc(detail(s))}</span></span>
          </label>`).join('')}
      </div>
      <label class="block mt-4">
        <span class="block text-sm text-slate-700">To confirm, type <b class="font-mono text-blush-700 select-none">${esc(s.confirm)}</b> below.</span>
        <input data-dz-confirm autocomplete="off" spellcheck="false" class="mt-1.5 w-full rounded-xl border border-blush-300 bg-white px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blush-300" placeholder="${esc(s.confirm)}" aria-label="Confirmation phrase">
      </label>
      <button data-dz-go disabled class="mt-3 w-full rounded-full bg-blush-700 text-white font-bold px-4 py-2.5 disabled:opacity-40 disabled:cursor-not-allowed hover:enabled:bg-blush-700/90">Erase and start over</button>
    </section>

    <p class="mt-3 text-xs text-slate-500">Backups (and bringing one back) are in <b>🗑 Trash &amp; backups</b>.</p>`;

  const confirmInput = pane.querySelector('[data-dz-confirm]'), go = pane.querySelector('[data-dz-go]');
  const picked = () => [...pane.querySelectorAll('[data-dz-part]:checked')].map(i => i.dataset.dzPart);
  // The agent goes with everything else: ticking it ticks (and locks) projects, focus, chat and settings.
  const WITH_AGENT = ['projects', 'focus', 'chat', 'settings'];
  const update = () => {
    const agentOn = pane.querySelector('[data-dz-part="agent"]').checked;
    WITH_AGENT.forEach(k => {
      const box = pane.querySelector(`[data-dz-part="${k}"]`);
      if (agentOn) box.checked = true;
      box.disabled = agentOn;
      box.closest('label').classList.toggle('opacity-70', agentOn);
      box.closest('label').title = agentOn ? 'Erased with the agent' : '';
    });
    const p = picked(), n = p.length;
    go.disabled = !n || confirmInput.value.trim().toLowerCase() !== s.confirm;
    go.textContent = agentOn ? `Erase everything${p.includes('shared') ? ' (and the shared memory)' : ''} and awaken a new agent`
      : n ? `Erase ${n} of ${DANGER_PARTS.length} and start over` : 'Pick something to erase';
  };
  pane.querySelectorAll('[data-dz-part]').forEach(i => i.onchange = update);
  confirmInput.oninput = update;
  confirmInput.onpaste = e => { e.preventDefault(); toast('Type it, don’t paste it.', 'info'); };   // a deliberate act
  update();
  go.onclick = async () => {
    const parts = picked();
    go.disabled = true;
    go.textContent = 'Backing up and erasing…';
    const r = await attempt(() => api('POST', '/api/reset', { parts, confirm: confirmInput.value }));
    if (!r) return update();
    // This browser's own leftovers: the timer points at old sessions and tasks; preferences reset with settings.
    try {
      Object.keys(localStorage).filter(k => k.startsWith('nestwork.')).forEach(k => {
        if (parts.includes('settings') || ['nestwork.hudTimer', 'nestwork.hudProject', 'nestwork.collapsed', 'nestwork.cols'].includes(k)) localStorage.removeItem(k);
      });
    } catch { /* private mode */ }
    sessionStorage.setItem('nestwork.afterReset', r.backup);
    location.hash = parts.includes('agent') ? '#/awaken' : '#/';
    location.reload();
  };
}

// Say what happened after the reload that follows a reset or restore.
window.addEventListener('DOMContentLoaded', () => {
  const reset = sessionStorage.getItem('nestwork.afterReset'), restored = sessionStorage.getItem('nestwork.afterRestore');
  sessionStorage.removeItem('nestwork.afterReset');
  sessionStorage.removeItem('nestwork.afterRestore');
  if (reset) setTimeout(() => toast(`Fresh start ✓. A backup of before is in Settings → 🗑 Trash & backups (${reset}).`, 'success'), 600);
  if (restored) setTimeout(() => toast(`Restored the backup from ${restored} ✓`, 'success'), 600);
});

// Settings → ⌨️ Shortcuts: every keyboard shortcut in the app, in one list. ⌘K finds each one too (spotlight.js),
// and ? opens this tab from anywhere (hud.js). Add a row here whenever a new shortcut is added.
const KB_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const KB_MOD = KB_MAC ? '⌘' : 'Ctrl';
// [section, where it works, [[keys, what it does, extra words for ⌘K]]].
const SHORTCUTS = [
  ['Anywhere', '', [
    [[KB_MOD, 'K'], 'Quick search: projects, to-dos, pages, settings, actions and these shortcuts', 'spotlight find search command palette'],
    [[KB_MOD, 'Z'], 'Undo the last change (yours or your agent’s)', 'undo revert back mistake'],
    [['V'], 'Switch view: HUD → Focus → Workflow → Agent’s Work', 'view page switch next'],
    [['C'], 'Open or close the chat', 'chat agent talk'],
    [['?'], 'Show these shortcuts', 'help keys keyboard shortcuts'],
    [['Esc'], 'Close the popup, menu or panel that’s open', 'close escape cancel'],
  ]],
  ['HUD', 'on the HUD', [
    [['F'], 'Start or pause the focus timer', 'pomodoro timer focus start pause'],
  ]],
  ['Project boards', 'inside a project', [
    [['N'], 'New to-do', 'add create task'],
    [[KB_MOD, 'click'], 'Open a to-do as its own page instead of the popup', 'open page link'],
  ]],
  ['Calendar', 'on Workflow → Calendar', [
    [['←', '→'], 'Previous / next day, week or month', 'previous next back forward'],
    [['T'], 'Jump to today', 'today now'],
    [['D'], 'Day view', 'day'], [['W'], 'Week view', 'week'], [['M'], 'Month view', 'month'],
    [['N'], 'Add something to the calendar', 'add new event schedule'],
    [['H'], 'History: what happened on past days', 'history past'],
    [['G'], 'Go to a date', 'go jump date'],
    [['click'], 'An empty time: add something there', 'add event'],
    [['double-click'], 'An empty day in month view: add something that day', 'add event month'],
  ]],
  ['Chat', 'in the chat box', [
    [['↵'], 'Send', 'send message enter'],
    [['Shift', '↵'], 'New line', 'new line newline'],
  ]],
  ['Notes', 'in a to-do’s notes', [
    [[KB_MOD, '↵'], 'Save the note', 'save note enter'],
    [['Esc'], 'Stop editing a note', 'cancel'],
  ]],
  ['Quick search', `in ${KB_MOD}K`, [
    [['↑', '↓'], 'Move through the results', 'up down'],
    [['↵'], 'Go there', 'open enter'],
  ]],
  ['Menus', 'right-click menus', [
    [['right-click'], 'Open a menu of what you can do with that thing', 'context menu'],
    [['↑', '↓', '←', '→'], 'Move through the menu and its submenus', 'arrow keys'],
    [['↵'], 'Pick', 'enter space'],
    [['Shift', 'right-click'], 'The browser’s own menu instead', 'browser copy inspect'],
  ]],
  ['Pictures', 'in a full-size picture', [
    [['←', '→'], 'Previous / next picture', 'gallery photos selfies'],
  ]],
  ['Filters', 'in a board’s filter box', [
    [['Esc'], 'Clear the filter', 'search filter clear'],
  ]],
];
const kbdSep = keys => /^[←↑]/.test(keys[0]) ? ' ' : ' + ';   // arrow keys are alternatives, the rest are held together
const kbdHTML = keys => keys.map(k => `<kbd class="hud-kbd">${esc(k)}</kbd>`).join(kbdSep(keys));

function shortcutsHTML() {
  return `
    <h3 class="text-sm font-bold text-slate-700 mb-1">Keyboard shortcuts</h3>
    <p class="text-xs text-slate-400 mb-4">Press <kbd class="hud-kbd">?</kbd> anywhere to come back here, or search any of these in <kbd class="hud-kbd">${KB_MOD}K</kbd>. Single-letter keys don’t work while you’re typing in a box.</p>
    <div class="grid sm:grid-cols-2 gap-x-6 gap-y-5">
      ${SHORTCUTS.map(([section, where, rows]) => `
        <section>
          <h4 class="text-xs font-bold uppercase tracking-wide text-lavender-700 mb-1.5">${esc(section)}${where ? ` <span class="font-normal normal-case tracking-normal text-slate-400">· ${esc(where)}</span>` : ''}</h4>
          <ul class="space-y-1">
            ${rows.map(([keys, what]) => `<li data-find class="flex items-baseline justify-between gap-3 text-sm text-slate-700 rounded-lg px-1">
              <span>${esc(what)}</span><span class="shrink-0 whitespace-nowrap">${kbdHTML(keys)}</span></li>`).join('')}
          </ul>
        </section>`).join('')}
    </div>`;
}

// Settings → How it works.
function helpHTML() {
  return `
    <h3 class="text-sm font-bold text-slate-700 mb-3">How Nestwork works</h3>
    <ol class="space-y-3 text-sm text-slate-700 list-decimal pl-5">
      <li><b>Everything is a block.</b> A project, a group like “Characters”, a character like “Cloud”, and a task like “Backstory” are all blocks.</li>
      <li><b>Blocks nest.</b> Click any card to open it. Inside you get a fresh board for the blocks it contains, as many levels deep as you like.</li>
      <li><b>Rules move things for you.</b> Open ⋯ → <i>Rules</i> and write them up front: “Move <i>2D sprites</i> to <i>Ready for work</i> when <i>Backstory</i> reaches <i>In review</i>”. When the rule is met the block moves by itself (⚡). Until then it can't be moved past that phase, and everything inside it waits too.</li>
      <li><b>Phases are yours.</b> Click <i>Phases</i> to rename, recolor, reorder, add or remove the columns. Mark one phase <i>🔍 Review</i>: a bubble whose to-dos are all complete waits there for your OK.</li>
      <li><b>To-dos inside to-dos are bubbles.</b> Give a to-do its own to-dos and it becomes a bubble on the board: its name sits in the column of its slowest to-do, and its outline stretches to its farthest one. Bubbles nest and hug each other, and reshape as you drag to-dos, so when every character's 3D model reaches review, <i>Characters</i> is there too. Click a to-do or a bubble's name to see and edit it in a popup; use <i>Move to…</i> to put it in another bubble.</li>
      <li><b>Progress rolls up.</b> A block can only be completed once everything inside it is complete, and its progress bar counts every level beneath it.</li>
      <li><b>Reuse structures.</b> Set up one character, then <i>Duplicate</i> it for the next. Links inside the copy follow along.</li>
      <li><b>One to-do at a time.</b> On the HUD, drag a to-do from <i>Today</i> onto the focus timer. It stays there session after session until its daily pomodoros are done (set it in the to-do's edit panel; new to-dos start at 0, which means no daily plan), then the timer lets go so you can drag in the next one.</li>
      <li><b>Find anything fast.</b> Press <kbd class="hud-kbd">${typeof SPOT_KEY === 'undefined' ? '⌘K' : SPOT_KEY}</kbd> anywhere to search every project, to-do, page and setting; ↵ takes you straight there.</li>
      <li><b>Keyboard shortcuts.</b> Press <kbd class="hud-kbd">?</kbd> anywhere, or see the <i>⌨️ Shortcuts</i> tab, for every one of them.</li>
      <li><b>Not sure what to do next?</b> The <i>What can I do now?</i> tab lists every task you can work on right now, across all levels.</li>
    </ol>`;
}

// ---------------------------------------------------------------- settings & AI agents
const ACCESS = [
  ['read_only', '👀 Read only', 'Agents can look at your projects and answer questions, but every change is refused.'],
  ['ask', '✋ Ask first', 'Agents can suggest changes. Each one waits below until you approve it; nothing changes before that.'],
  ['full', '⚡ Full access', 'Agents change things directly. Rules still apply: they can’t move anything your rules don’t allow.'],
];
const EFFORTS = [
  ['', 'Default (Claude Code decides)'], ['low', 'Low: quickest'], ['medium', 'Medium'],
  ['high', 'High'], ['xhigh', 'Extra high'], ['max', 'Max: slowest, deepest'],
];
const TOOL_LEVELS = [
  ['contained', 'Contained', 'Only Nestwork and its memory files.'],
  ['web', '+ Web', 'Also web search and reading web pages.'],
  ['broad', 'Broad', 'Most Claude Code tools, including skills, running commands, reading other files on this computer and using your Chrome (with the Claude extension).'],
];
const FILES = [
  ['shared_claude', 'Shared CLAUDE.md', 'Read by every Claude session on this computer. Holds “About me” and loads memory.md.'],
  ['memory', 'memory.md', 'Long-term memory shared by every Claude session. The agent adds to it when you share preferences.'],
  ['agent_claude', 'Agent CLAUDE.md', 'Only the Nestwork agent reads this: its persona and how it works.'],
];
const THEME_CHOICES = [['auto', '◐ Auto', 'Follow your computer'], ['light', '☀︎ Light', ''], ['dark', '☾ Dark', '']];
const settingsUI = { onFilesChanged: null };
const REMINDER_CHOICES = { day_before: 'The day before (9:00)', morning: 'The morning of (8:00)', '1h': '1 hour before', '15m': '15 minutes before' };
const REMINDER_DEFAULT = ['day_before', 'morning', '1h'];

async function settingsModal(tab = 'agent') {
  const [settings, proposals, agentData, reminderData, look, closet] = await Promise.all([
    api('GET', '/api/settings'), api('GET', '/api/proposals'), api('GET', '/api/agent/files'), api('GET', '/api/reminders'),
    api('GET', '/api/awakening'), api('GET', '/api/agent/wardrobe')]);
  const tabs = [['agent', '✦ Agent'], ['memory', '🧠 Memory files'], ['access', '🤖 AI access'], ['timer', '⏱ Focus timer'], ['appearance', '🎨 Appearance'], ['sounds', '🔊 Sounds'], ['help', '❔ How it works'], ['shortcuts', '⌨️ Shortcuts'], ['phone', '📱 Phone'], ['trash', '🗑 Trash & backups'], ['danger', '⚠️ Danger zone']];
  const radioCard = (name, value, on, title, help) => `
    <label class="flex items-start gap-3 rounded-2xl border p-3 cursor-pointer ${on ? 'border-lavender-300 bg-lavender-50' : 'border-slate-200 hover:bg-slate-50'}">
      <input type="radio" name="${name}" value="${value}" ${on ? 'checked' : ''} class="mt-1 text-lavender-700">
      <span><span class="block text-sm font-semibold">${title}</span>${help ? `<span class="block text-xs text-slate-500">${help}</span>` : ''}</span>
    </label>`;
  const fieldInput = (block, f, value) => `
    <label class="block">
      <span class="block text-xs font-semibold text-slate-600 mb-1">${esc(f.label)}</span>
      ${fieldControl(`data-field="${block}:${f.key}"`, f, value, 'w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-lavender-300')}
    </label>`;
  const fieldsForm = (block, title, help) => `
    <section class="rounded-2xl border border-slate-200 p-4">
      <div class="flex items-start justify-between gap-3">
        <div><h3 class="text-sm font-bold text-slate-700">${title}</h3><p class="text-xs text-slate-500">${help}</p></div>
        <button data-save-fields="${block}" class="shrink-0 rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-semibold px-3 py-1.5">Save</button>
      </div>
      <div class="mt-3 grid sm:grid-cols-2 gap-3" data-fields="${block}">
        ${agentData.field_defs[block].map(f => fieldInput(block, f, agentData.fields[block][f.key] || '')).join('')}
      </div>
    </section>`;

  openModal(`
    <div class="shrink-0 px-6 pt-5">
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-lg font-bold">Settings</h2>
      <button data-close class="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="Close">✕</button>
    </div>
    <div class="flex flex-wrap gap-1 border-b border-slate-200" role="tablist">
      ${tabs.map(([k, l]) => `<button data-tab="${k}" role="tab" class="st-tab px-3 py-2 text-sm font-medium border-b-2 -mb-px">${l}${k === 'access' && proposals.length ? ` <span class="ml-1 rounded-full bg-blush-300 text-white text-[11px] px-1.5">${proposals.length}</span>` : ''}</button>`).join('')}
    </div>
    </div>

    <div class="st-body flex-1 min-h-0 overflow-y-auto overscroll-contain scroll-thin px-6 pt-4 pb-6">
    <div data-pane="agent" class="space-y-4">
      ${appearanceSectionHTML()}
      ${wardrobeSectionHTML()}
      ${picturesSectionHTML()}
      ${fieldsForm('about', '🙋 About you', 'Saved in the shared CLAUDE.md, so every Claude session knows this.')}
      ${fieldsForm('persona', '✦ Your agent', 'Saved in the agent’s own CLAUDE.md: only the Nestwork agent takes on this name and personality. Pick a suggestion or type your own.')}
      <section class="rounded-2xl border border-slate-200 p-4">
        <h3 class="text-sm font-bold text-slate-700">⏰ Reminders</h3>
        <p class="text-xs text-slate-500">To-dos with a due date remind you in the chat (choose when in each to-do), and the agent can set reminders when you ask. Nestwork has to be running for them to arrive.</p>
        <div class="mt-2 grid sm:grid-cols-2 gap-2">
          ${radioCard('reminder_style', 'quick', settings.reminder_style !== 'agent', '⚡ Quick', 'Written by Nestwork: instant and free.')}
          ${radioCard('reminder_style', 'agent', settings.reminder_style === 'agent', '✦ Written by your agent', 'In its own voice, with what to prepare. Uses Claude for each reminder.')}
        </div>
        <div class="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <span class="text-slate-600">Desktop notifications:</span>
          <span id="st-notif" class="text-slate-500"></span>
        </div>
        <details class="mt-3">
          <summary class="text-xs font-semibold text-slate-600 cursor-pointer">Coming up in the next 14 days (${reminderData.upcoming.length})</summary>
          <ul class="mt-2 space-y-1" id="st-upcoming">
            ${reminderData.upcoming.map(u => `<li class="flex items-center gap-2 text-xs text-slate-600">
              <span class="font-mono text-slate-400 shrink-0">${esc(u.at.replace('T', ' '))}</span>
              <span class="flex-1 min-w-0 truncate">${u.kind === 'due' ? `${esc(u.choice)}: <b>${esc(u.title)}</b> · ${esc(u.project)}` : `${esc(u.text)}${u.title ? ` · <b>${esc(u.title)}</b>` : ''} <span class="text-slate-400">(${esc(u.created_by)})</span>`}</span>
              ${u.kind === 'custom' ? `<button data-del-reminder="${u.id}" class="text-blush-700 hover:underline">Remove</button>` : ''}
            </li>`).join('') || '<li class="text-xs text-slate-400">Nothing scheduled.</li>'}
          </ul>
        </details>
      </section>
      <section class="rounded-2xl border border-slate-200 p-4">
        <h3 class="text-sm font-bold text-slate-700">🧰 What the agent can use</h3>
        <div class="mt-2 grid sm:grid-cols-3 gap-2">${TOOL_LEVELS.map(([k, l, h]) => radioCard('tools', k, settings.agent_tools === k, l, h)).join('')}</div>
        <label class="mt-3 block max-w-xs">
          <span class="block text-xs font-semibold text-slate-600 mb-1">Model</span>
          ${modelSelect('st-model', settings.agent_model, 'w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm')}
        </label>
        <label class="mt-3 block max-w-xs">
          <span class="block text-xs font-semibold text-slate-600 mb-1">Effort</span>
          <select id="st-effort" class="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm">
            ${EFFORTS.map(([k, l]) => `<option value="${k}" ${k === (settings.agent_effort || '') ? 'selected' : ''}>${l}</option>`).join('')}
          </select>
          <span class="block text-[11px] text-slate-400 mt-1">How hard it thinks. Higher is smarter but slower and uses more of your plan.</span>
        </label>
      </section>
      <section class="rounded-2xl border border-slate-200 p-4" data-apps><p class="text-xs text-slate-400">Loading connected apps…</p></section>
    </div>

    <div data-pane="memory" class="hidden">
      <div class="flex flex-wrap gap-1.5 mb-3">${FILES.map(([k, l]) => `<button data-file="${k}" class="st-file rounded-full border px-3 py-1 text-xs font-semibold">${l}</button>`).join('')}</div>
      <p id="mf-help" class="text-xs text-slate-500"></p>
      <p id="mf-path" class="text-[11px] text-slate-400 font-mono break-all mb-2"></p>
      <div id="mf-alert" class="hidden mb-2 rounded-xl bg-butter-50 border border-butter-200 px-3 py-2 text-xs text-butter-700"></div>
      <textarea id="mf-text" spellcheck="false" class="w-full h-[45vh] rounded-xl border border-slate-300 bg-slate-50 p-3 font-mono text-xs leading-5 focus:outline-none focus:ring-2 focus:ring-lavender-300"></textarea>
      <div class="mt-2 flex items-center justify-between gap-2">
        <span id="mf-status" class="text-xs text-slate-400"></span>
        <div class="flex gap-2">
          <button id="mf-revert" class="rounded-full px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100">Revert</button>
          <button id="mf-save" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-semibold px-4 py-1.5">Save file</button>
        </div>
      </div>
    </div>

    <div data-pane="access" class="hidden">
      <p class="text-xs text-slate-500 mb-2">For the Nestwork chat agent and any other AI app connected through the MCP server. Reading is always allowed.</p>
      <div class="space-y-2" id="st-access">
        ${ACCESS.map(([key, label, help]) => radioCard('access', key, settings.agent_access === key, label + (key === 'ask' ? ' <span class="text-xs font-normal text-slate-400">(recommended)</span>' : ''), help)).join('')}
      </div>
      <label class="mt-3 flex items-start gap-3 rounded-2xl border border-slate-200 p-3 cursor-pointer hover:bg-slate-50">
        <input type="checkbox" id="st-delete" ${settings.allow_delete ? 'checked' : ''} class="mt-1 rounded text-blush-700">
        <span><span class="block text-sm font-semibold">Let agents delete blocks</span>
        <span class="block text-xs text-slate-500">Off by default. Deleting removes a block and everything inside it and can’t be undone.</span></span>
      </label>
      <div class="mt-6 flex items-center justify-between">
        <h3 class="text-sm font-bold text-slate-700">📥 Proposed changes <span id="st-count" class="font-normal text-slate-400"></span></h3>
        <div id="st-bulk" class="flex gap-2 text-xs"></div>
      </div>
      <div id="st-proposals" class="mt-2 space-y-2"></div>
      <details class="mt-6 rounded-2xl border border-slate-200 p-3">
        <summary class="text-sm font-bold text-slate-700 cursor-pointer">🔌 Connect other Claude apps</summary>
        <p class="text-xs text-slate-500 mt-2"><b>Claude Code:</b> run <code>claude</code> inside the Nestwork folder (it has a <code>.mcp.json</code>), or add it for every folder:</p>
        <pre class="mt-1 rounded-xl bg-slate-100 p-2 text-[11px] whitespace-pre-wrap break-all select-all">${esc(settings.mcp_command)}</pre>
        <p class="text-xs text-slate-500 mt-2">The README has the Claude Desktop setup too.</p>
      </details>
    </div>

    <div data-pane="timer" class="hidden">${timerSettingsHTML()}</div>

    <div data-pane="help" class="hidden">${helpHTML()}</div>
    <div data-pane="shortcuts" class="hidden">${shortcutsHTML()}</div>
    <div data-pane="phone" class="hidden"><p class="text-sm text-slate-400">Loading…</p></div>
    <div data-pane="trash" class="hidden"><p class="text-sm text-slate-400">Loading…</p></div>
    <div data-pane="sounds" class="hidden"><p class="text-sm text-slate-400">Loading…</p></div>
    <div data-pane="danger" class="hidden"><p class="text-sm text-slate-400">Loading…</p></div>

    <div data-pane="appearance" class="hidden">
      <h3 class="text-sm font-bold text-slate-700 mb-2">Theme</h3>
      <div class="grid sm:grid-cols-3 gap-2">${THEME_CHOICES.map(([k, l, h]) => radioCard('theme', k, theme === k, l, h)).join('')}</div>
    </div>
    </div>`, root => {
    const $ = q => root.querySelector(q);
    const paintRadios = name => root.querySelectorAll(`[name=${name}]`).forEach(i => {
      i.closest('label').className = `flex items-start gap-3 rounded-2xl border p-3 cursor-pointer ${i.checked ? 'border-lavender-300 bg-lavender-50' : 'border-slate-200 hover:bg-slate-50'}`;
    });

    // Tabs
    const showTab = k => {
      root.querySelectorAll('.st-tab').forEach(b => {
        const on = b.dataset.tab === k;
        b.className = `st-tab px-3 py-2 text-sm font-medium border-b-2 -mb-px ${on ? 'border-lavender-700 text-lavender-800' : 'border-transparent text-slate-500 hover:text-slate-800'}`;
      });
      root.querySelectorAll('[data-pane]').forEach(p => p.classList.toggle('hidden', p.dataset.pane !== k));
      root.querySelector('.st-body').scrollTop = 0;
      animate(root.querySelector(`[data-pane="${k}"]`), 'view-in');
    };
    root.querySelectorAll('.st-tab').forEach(b => b.onclick = () => showTab(b.dataset.tab));
    showTab(tab);

    bindTimerSettings(root.querySelector('[data-pane="timer"]'));
    dangerZone(root.querySelector('[data-pane="danger"]'));
    connectedApps(root.querySelector('[data-apps]'));
    trashPane(root.querySelector('[data-pane="trash"]'));
    soundsPane(root.querySelector('[data-pane="sounds"]'));
    // Only the Mac manages phone access; on a paired phone the tab says so.
    phonePane(root.querySelector('[data-pane="phone"]'));
    bindAppearanceSection(root, look);
    bindWardrobeSection(root, closet);
    bindPicturesSection(root, settings);

    // Agent: regex-backed fields, tools, model
    let data = agentData;
    const dirtyFields = new Set();
    root.querySelectorAll('[data-field]').forEach(i => i.addEventListener('input', () => dirtyFields.add(i.dataset.field.split(':')[0])));
    root.querySelectorAll('[data-save-fields]').forEach(b => b.onclick = async () => {
      const block = b.dataset.saveFields;
      const values = Object.fromEntries([...root.querySelectorAll(`[data-field^="${block}:"]`)].map(i => [i.dataset.field.split(':')[1], i.value]));
      const r = await attempt(() => api('PUT', `/api/agent/fields/${block}`, { values }));
      if (!r) return;
      data = r; dirtyFields.delete(block);
      toast(block === 'about' ? 'Saved to the shared CLAUDE.md.' : 'Saved to the agent’s CLAUDE.md.', 'success');
      loadFile(currentFile, true);
      window.refreshChatProfile?.();
    });
    root.querySelectorAll('[name=tools]').forEach(r => r.onchange = async () => {
      if (await attempt(() => api('PUT', '/api/settings', { agent_tools: r.value }))) paintRadios('tools');
    });
    $('#st-model').onchange = e => attempt(() => api('PUT', '/api/settings', { agent_model: e.target.value }));
    $('#st-effort').onchange = e => attempt(() => api('PUT', '/api/settings', { agent_effort: e.target.value }));
    root.querySelectorAll('[name=reminder_style]').forEach(r => r.onchange = async () => {
      if (await attempt(() => api('PUT', '/api/settings', { reminder_style: r.value }))) paintRadios('reminder_style');
    });
    const notif = $('#st-notif');
    const drawNotif = () => {
      if (!('Notification' in window)) { notif.textContent = 'not supported in this browser'; return; }
      if (Notification.permission === 'granted') notif.innerHTML = '<span class="text-mint-700 font-semibold">on ✓</span>';
      else if (Notification.permission === 'denied') notif.textContent = 'blocked in your browser settings';
      else {
        notif.innerHTML = '<button class="rounded-full border border-lavender-200 px-2.5 py-0.5 text-lavender-700 hover:bg-lavender-50">Turn on</button>';
        notif.querySelector('button').onclick = async () => { await Notification.requestPermission(); drawNotif(); };
      }
    };
    drawNotif();
    $('#st-upcoming').onclick = async e => {
      const b = e.target.closest('[data-del-reminder]');
      if (b && await attempt(() => api('DELETE', `/api/reminders/${b.dataset.delReminder}`))) animateOut([b.closest('li')], 'poof', 200).then(() => b.closest('li').remove());
    };

    // Memory files: edit directly; changes the agent makes show up here
    let currentFile = 'memory', loaded = null;
    const editor = $('#mf-text');
    const dirty = () => loaded && editor.value !== loaded.content;
    const loadFile = (name, quiet = false) => {
      currentFile = name;
      loaded = data.files[name];
      editor.value = loaded.content;
      $('#mf-path').textContent = loaded.path;
      $('#mf-help').textContent = FILES.find(f => f[0] === name)[2];
      $('#mf-alert').classList.add('hidden');
      $('#mf-status').textContent = '';
      root.querySelectorAll('.st-file').forEach(b => {
        const on = b.dataset.file === name;
        b.className = `st-file rounded-full border px-3 py-1 text-xs font-semibold ${on ? 'bg-lavender-100 border-lavender-300 text-lavender-800' : 'border-slate-200 text-slate-500 hover:bg-slate-50'}`;
      });
      if (!quiet) animate(editor, 'page-fade');
    };
    root.querySelectorAll('.st-file').forEach(b => b.onclick = () => {
      if (dirty() && !confirm('Discard your unsaved changes to this file?')) return;
      loadFile(b.dataset.file);
    });
    $('#mf-revert').onclick = () => loadFile(currentFile);
    $('#mf-save').onclick = async () => {
      try {
        data = await api('PUT', `/api/agent/files/${currentFile}`, { content: editor.value, base_mtime: loaded.mtime });
      } catch (e) {
        $('#mf-alert').innerHTML = `${esc(e.message)} <button class="underline font-semibold" data-reload>Reload</button> or <button class="underline" data-overwrite>save anyway</button>`;
        $('#mf-alert').classList.remove('hidden');
        return;
      }
      loadFile(currentFile, true);
      $('#mf-status').textContent = 'Saved ✓';
      animate($('#mf-status'), 'fade-in');
      window.refreshChatProfile?.();
      fillFields();
    };
    $('#mf-alert').onclick = async e => {
      if (e.target.closest('[data-reload]')) { data = await api('GET', '/api/agent/files'); loadFile(currentFile); }
      if (e.target.closest('[data-overwrite]')) { loaded = { ...loaded, mtime: null }; $('#mf-save').click(); }
    };
    const fillFields = () => {
      for (const block of ['about', 'persona']) {
        if (dirtyFields.has(block)) continue;
        root.querySelectorAll(`[data-field^="${block}:"]`).forEach(i => { i.value = data.fields[block][i.dataset.field.split(':')[1]] || ''; });
      }
    };
    loadFile('memory', true);
    // Called by the poll loop when any of the files changed on disk (e.g. the agent remembered something).
    settingsUI.onFilesChanged = async () => {
      if (!document.body.contains(root)) { settingsUI.onFilesChanged = null; return; }
      const fresh = await api('GET', '/api/agent/files');
      const changed = fresh.files[currentFile].mtime !== loaded.mtime;
      data = fresh;
      fillFields();
      if (!changed) return;
      if (dirty()) {
        $('#mf-alert').innerHTML = `This file was just changed outside this editor (perhaps by the agent). <button class="underline font-semibold" data-reload>Reload it</button> (your unsaved edits will be lost).`;
        $('#mf-alert').classList.remove('hidden');
      } else {
        loadFile(currentFile, true);
        $('#mf-status').textContent = 'Updated just now from disk (e.g. by the agent)';
        animate(editor, 'land');
      }
    };

    // AI access
    root.querySelectorAll('[name=access]').forEach(r => r.onchange = async () => {
      if (await attempt(() => api('PUT', '/api/settings', { agent_access: r.value }))) {
        paintRadios('access');
        toast(`Agent access: ${ACCESS.find(a => a[0] === r.value)[1]}`, 'success');
      }
    });
    $('#st-delete').onchange = e => attempt(() => api('PUT', '/api/settings', { allow_delete: e.target.checked }));
    drawProposals(root, proposals);

    // Appearance
    root.querySelectorAll('[name=theme]').forEach(r => r.onchange = () => { setTheme(r.value); paintRadios('theme'); });
  }, { wide: true });
}

function drawProposals(root, proposals) {
  const box = root.querySelector('#st-proposals');
  root.querySelector('#st-count').textContent = proposals.length ? `(${proposals.length})` : '';
  root.querySelector('#st-bulk').innerHTML = proposals.length > 1
    ? `<button data-all="approve" class="text-mint-700 underline">Approve all</button><button data-all="reject" class="text-blush-700 underline">Reject all</button>` : '';
  box.innerHTML = proposals.length ? proposals.map(p => `
    <div class="proposal rounded-2xl border border-lavender-100 bg-lavender-50/60 p-3" data-id="${p.id}">
      <p class="text-sm text-slate-700">${esc(p.summary)}</p>
      <p class="text-[11px] text-slate-400 mt-0.5">#${p.id} · ${esc(p.agent)} · ${esc(p.created_at)} UTC</p>
      <div class="mt-2 flex gap-2">
        <button data-approve class="rounded-full bg-mint-100 text-mint-700 border border-mint-200 px-3 py-1 text-xs font-semibold hover:bg-mint-200">✓ Approve</button>
        <button data-reject class="rounded-full bg-white text-blush-700 border border-blush-200 px-3 py-1 text-xs font-semibold hover:bg-blush-50">✕ Reject</button>
      </div>
    </div>`).join('')
    : `<p class="text-sm text-slate-400 rounded-2xl border border-dashed border-slate-200 p-3">Nothing waiting. When agent access is “Ask first”, changes agents suggest show up here.</p>`;
  const decide = async (id, approve) => {
    const el = box.querySelector(`.proposal[data-id="${id}"]`);
    const r = await attempt(() => api('POST', `/api/proposals/${id}/${approve ? 'approve' : 'reject'}`));
    if (!r) return false;
    if (el) await animateOut([el], 'poof', 200);
    return true;
  };
  box.onclick = async e => {
    const el = e.target.closest('.proposal'); if (!el) return;
    const approve = !!e.target.closest('[data-approve]');
    if (!approve && !e.target.closest('[data-reject]')) return;
    if (await decide(Number(el.dataset.id), approve)) refreshAfterAgents(root);
  };
  root.querySelector('#st-bulk').onclick = async e => {
    const which = e.target.closest('[data-all]')?.dataset.all; if (!which) return;
    for (const p of [...proposals].reverse()) await decide(p.id, which === 'approve');   // oldest first
    refreshAfterAgents(root);
  };
}

// After approving/rejecting: update the inbox in place and quietly redraw the page underneath.
async function refreshAfterAgents(root) {
  drawProposals(root, await api('GET', '/api/proposals'));
  await renderCurrent();
  await poll(true);   // update the badge and remember the new version
}

// ---------------------------------------------------------------- Settings → 📱 Phone
// Reach Nestwork from your phone through Tailscale (a private network of only your devices), then pair the
// phone with a one-time code. Guided steps, the current one highlighted. Server side: phone.py, devices.py.
async function phonePane(pane) {
  let d;
  try { d = await api('GET', '/api/phone'); }
  catch (e) { pane.innerHTML = `<p class="text-sm text-blush-700">${esc(e.message)}</p>`; return; }
  const ts = d.tailscale;
  const step = !ts.installed || !ts.running ? 1 : !d.devices.length && !ts.https_ready ? 3 : !ts.serving ? 4 : 5;
  const btn = 'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold';
  const primary = `${btn} bg-lavender-700 text-white hover:bg-lavender-800`, ghost = `${btn} border border-lavender-300 text-lavender-700 hover:bg-lavender-50`;
  const when = iso => iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'never';
  const stepBox = (n, title, body, done = n < step) => `
    <li class="flex gap-3 rounded-xl border ${n === step ? 'border-lavender-300 bg-lavender-50/60' : 'border-slate-200'} p-3">
      <span class="shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${done ? 'bg-mint-100 text-mint-700' : n === step ? 'bg-lavender-700 text-white' : 'bg-slate-100 text-slate-500'}">${done ? '✓' : n}</span>
      <div class="min-w-0 flex-1"><p class="text-sm font-bold text-slate-700">${title}</p><div class="mt-1 space-y-1.5 text-xs text-slate-600">${body}</div></div>
    </li>`;
  pane.innerHTML = `
    <section class="rounded-2xl border border-slate-200 p-4">
      <h3 class="text-sm font-bold text-slate-700">📱 Use Nestwork on your phone</h3>
      <p class="text-xs text-slate-500">Your phone reaches this Mac through <b>Tailscale</b>, a free private network that only your own devices can join: nothing is put on the internet, and the connection is encrypted. Then you pair the phone once with a code, and add Nestwork to its Home Screen so it opens like an app.</p>
      <ol class="mt-3 space-y-2">
        ${stepBox(1, 'Install Tailscale on this Mac and sign in', !ts.installed ? `
          <p>Download it, open it, and sign in from its icon in the menu bar (Google, Apple or Microsoft account).</p>
          <p class="flex flex-wrap gap-2"><a href="https://tailscale.com/download/mac" target="_blank" rel="noopener" class="${primary}">Download Tailscale for Mac ↗</a><button data-ph-check class="${ghost}">Check again</button></p>`
          : !ts.running ? `<p>Tailscale is installed but not signed in or turned on. Click its icon in the menu bar and sign in (or turn it on).</p>
          ${ts.login_url ? `<p><a href="${esc(ts.login_url)}" target="_blank" rel="noopener" class="${primary}">Sign in ↗</a></p>` : ''}<p><button data-ph-check class="${ghost}">Check again</button></p>`
          : `<p>Signed in. This Mac is <code>${esc(ts.dns_name || '')}</code> on your Tailscale network.</p>`)}
        ${stepBox(2, 'Install Tailscale on your phone', `
          <p>Get the Tailscale app, sign in with the <b>same account</b>, and allow the VPN when it asks (that’s how it joins your private network). Keep it switched on when you want to use Nestwork.</p>
          <p class="flex flex-wrap gap-2"><a href="https://apps.apple.com/app/tailscale/id1470499037" target="_blank" rel="noopener" class="${ghost}">App Store ↗</a><a href="https://play.google.com/store/apps/details?id=com.tailscale.ipn" target="_blank" rel="noopener" class="${ghost}">Google Play ↗</a></p>`, step > 2 && d.devices.length > 0)}
        ${stepBox(3, 'Turn on HTTPS for your Tailscale network (once)', ts.https_ready ? '<p>Done: your network can give this Mac a secure https address.</p>' : `
          <p>In the Tailscale admin page, under <b>DNS</b>: make sure <b>MagicDNS</b> is on, then click <b>Enable HTTPS</b>. It lets your phone install Nestwork as an app and keeps everything encrypted.</p>
          <p class="flex flex-wrap gap-2"><a href="https://login.tailscale.com/admin/dns" target="_blank" rel="noopener" class="${primary}">Open Tailscale DNS settings ↗</a><button data-ph-check class="${ghost}">Check again</button></p>`, ts.https_ready)}
        ${stepBox(4, 'Turn on phone access', ts.serving ? `
          <p>On: Nestwork is at <code>${esc(ts.url)}</code> for your Tailscale devices only (and only paired ones can use it).</p>
          <p><button data-ph-off class="${btn} border border-slate-300 text-slate-600 hover:bg-slate-50">Turn off</button></p>` : `
          <p>Gives Nestwork its address on your Tailscale network. Nestwork still only listens on this Mac; Tailscale passes your phone’s requests on.</p>
          <p class="flex flex-wrap gap-2"><button data-ph-on ${ts.running ? '' : 'disabled'} class="${primary} disabled:opacity-40">Turn on phone access</button></p>
          <p data-ph-consent class="hidden"></p>`, ts.serving)}
        ${stepBox(5, 'Pair your phone', `
          <p>Click <b>Pair a phone</b>, then point your phone’s camera at the code and tap the link. When it says <i>Connected</i>: tap <b>Share</b> → <b>Add to Home Screen</b> → <b>Add</b>, and open Nestwork from your Home Screen. No camera? Open the address above on the phone and type the code.</p>
          <p><button data-ph-pair ${ts.serving ? '' : 'disabled'} class="${primary} disabled:opacity-40">Pair a phone</button></p>
          <div data-ph-qr class="hidden"></div>`, false)}
      </ol>
    </section>

    <section class="mt-4 rounded-2xl border border-slate-200 p-4">
      <h3 class="text-sm font-bold text-slate-700">Paired devices</h3>
      ${d.devices.length ? `<ul class="mt-2 space-y-1.5">${d.devices.map(x => `
        <li class="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 px-3 py-2" data-dev="${x.id}">
          <span class="flex-1 min-w-0"><span class="block text-sm font-semibold text-slate-700 truncate">${esc(x.name)}</span>
            <span class="block text-[11px] text-slate-400">paired ${esc(when(x.created_at))} · last used ${esc(when(x.last_seen))}</span></span>
          <button data-dev-rename class="text-xs text-slate-500 hover:text-lavender-700">Rename</button>
          <button data-dev-revoke class="text-xs text-blush-700 hover:underline">Disconnect</button>
        </li>`).join('')}</ul>` : '<p class="mt-1 text-xs text-slate-400">None yet.</p>'}
      <p class="mt-2 text-xs text-slate-500">Lost a phone? Disconnect it here: its key stops working at once.</p>
    </section>

    <section class="mt-4 rounded-2xl border border-slate-200 p-4">
      <label class="flex items-start gap-3 cursor-pointer">
        <input type="checkbox" data-ph-awake ${d.keep_awake ? 'checked' : ''} ${d.keep_awake_available ? '' : 'disabled'} class="mt-0.5">
        <span><span class="block text-sm font-semibold text-slate-700">Keep this Mac awake while it’s plugged in</span>
        <span class="block text-xs text-slate-500">Your phone can only reach Nestwork while this Mac is on and Nestwork is running. This stops the Mac from sleeping on power (the screen can still turn off), while Nestwork runs.${d.keep_awake_available ? '' : ' (Only on macOS.)'}</span></span>
      </label>
    </section>`;

  const again = () => phonePane(pane);
  pane.querySelectorAll('[data-ph-check]').forEach(b => b.onclick = () => { b.textContent = 'Checking…'; again(); });
  pane.querySelector('[data-ph-on]')?.addEventListener('click', async e => {
    e.target.disabled = true; e.target.textContent = 'Turning on…';
    const r = await attempt(() => api('POST', '/api/phone/enable'));
    if (r?.ok) { toast(`Phone access is on: ${r.tailscale.url}`, 'success'); return again(); }
    const box = pane.querySelector('[data-ph-consent]');
    box.classList.remove('hidden');
    box.innerHTML = r?.consent_url
      ? `Tailscale asks you to allow this first: <a href="${esc(r.consent_url)}" target="_blank" rel="noopener" class="underline font-semibold">Allow in Tailscale ↗</a>, then <button data-ph-retry class="underline font-semibold">try again</button>.`
      : `<span class="text-blush-700">${esc(r?.message || 'That didn’t work.')}</span> <button data-ph-retry class="underline font-semibold">Try again</button>`;
    box.querySelector('[data-ph-retry]').onclick = again;
    e.target.disabled = false; e.target.textContent = 'Turn on phone access';
  });
  pane.querySelector('[data-ph-off]')?.addEventListener('click', async () => {
    if (confirm('Turn off phone access? Paired phones stay paired and work again when you turn it back on.') && await attempt(() => api('POST', '/api/phone/disable'))) again();
  });
  pane.querySelector('[data-ph-pair]')?.addEventListener('click', async () => {
    const r = await attempt(() => api('POST', '/api/phone/pair-code'));
    if (!r) return;
    const box = pane.querySelector('[data-ph-qr]');
    box.classList.remove('hidden');
    box.innerHTML = `<div class="mt-2 flex flex-wrap items-center gap-4 rounded-xl bg-white p-3 border border-slate-200">
      <div class="shrink-0 rounded bg-white p-1">${r.qr_svg}</div>
      <div class="min-w-0"><p class="text-xs text-slate-500">Or type this code on the phone:</p>
        <p class="hud-num text-2xl font-extrabold tracking-widest text-slate-800">${esc(r.code)}</p>
        <p class="text-[11px] text-slate-400" data-ph-left></p></div></div>`;
    const tick = () => {
      const left = Math.round(r.expires_at - Date.now() / 1000), el = box.querySelector('[data-ph-left]');
      if (!el) return clearInterval(timer);
      if (left <= 0) { box.innerHTML = '<p class="mt-2 text-xs text-slate-500">That code expired. Click <b>Pair a phone</b> for a new one.</p>'; return clearInterval(timer); }
      el.textContent = `Works once, for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} more.`;
    };
    const timer = setInterval(tick, 1000); tick();
    // Refresh the device list when the phone shows up.
    const watch = setInterval(async () => {
      if (!pane.isConnected) return clearInterval(watch);
      const now = await api('GET', '/api/phone').catch(() => null);
      if (now && now.devices.length > d.devices.length) { clearInterval(watch); clearInterval(timer); toast(`Paired: ${now.devices[now.devices.length - 1].name} ✓`, 'success'); again(); }
    }, 3000);
  });
  pane.querySelectorAll('[data-dev-rename]').forEach(b => b.onclick = async () => {
    const li = b.closest('[data-dev]'), name = prompt('Name this device:', li.querySelector('span span').textContent);
    if (name?.trim() && await attempt(() => api('PATCH', `/api/devices/${li.dataset.dev}`, { name: name.trim() }))) again();
  });
  pane.querySelectorAll('[data-dev-revoke]').forEach(b => b.onclick = async () => {
    const li = b.closest('[data-dev]');
    if (confirm(`Disconnect “${li.querySelector('span span').textContent}”? It will need a new pairing code to connect again.`)
      && await attempt(() => api('DELETE', `/api/devices/${li.dataset.dev}`))) again();
  });
  pane.querySelector('[data-ph-awake]').onchange = e => attempt(() => api('PUT', '/api/phone/keep-awake', { on: e.target.checked }));
}
