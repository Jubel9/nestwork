// Popups and panels for a block: the modal/drawer stack, the edit drawer (details, repeat, estimate,
// priority, tags, rules), the rule block picker, finished-bubble approvals, the block popup, phases editor,
// archive and import. Uses app.js and board.js.

// ---------------------------------------------------------------- modals & drawer
// Popups stack: with `stack`, a new one opens on top (e.g. the edit panel over a block popup) and
// closing it reveals the one below. Without `stack`, it replaces whatever was open.
function openModal(html, onMount, { drawer = false, wide = false, xl = false, stack = false } = {}) {
  if (!stack) modalRoot.innerHTML = '';
  const size = drawer ? 'w-full max-w-md h-full overflow-y-auto p-5'
    : wide ? 'w-full max-w-3xl rounded-2xl my-auto flex flex-col h-[min(46rem,calc(100dvh-2rem))] overflow-clip'
    : xl ? 'w-full max-w-6xl rounded-3xl p-5 sm:p-6 my-auto max-h-[calc(100dvh-2rem)] overflow-y-auto overscroll-contain scroll-thin'
    : 'w-full max-w-xl rounded-2xl p-6 my-auto max-h-[calc(100dvh-2rem)] overflow-y-auto overscroll-contain scroll-thin';
  modalRoot.insertAdjacentHTML('beforeend', `
    <div class="fixed inset-0 z-40 bg-black/40 flex ${drawer ? 'justify-end' : 'justify-center p-4 overflow-y-auto'}" data-backdrop ${drawer ? 'data-drawer' : ''}>
      <div class="relative bg-white shadow-xl ${size}" role="dialog" aria-modal="true">${html}</div>
    </div>`);
  syncDrawerClass();
  const root = modalRoot.lastElementChild;
  animate(root, 'fade-in');
  animate(root.firstElementChild, drawer ? 'slide-in' : 'zoom-in');
  root.addEventListener('mousedown', e => { if (e.target === root) closeModal(); });
  root.querySelectorAll('[data-close]').forEach(b => b.onclick = closeModal);
  onMount?.(root);
}
function openLayers() { return [...modalRoot.querySelectorAll(':scope > [data-backdrop]:not(.closing)')]; }
function syncDrawerClass() {   // the chat panel moves to the left of an open side panel
  document.body.classList.toggle('drawer-open', openLayers().some(l => l.hasAttribute('data-drawer')));
}
function closeModal() {   // closes the top popup
  const root = openLayers().pop();
  if (!root) return;
  root.classList.add('closing', 'pointer-events-none');
  syncDrawerClass();
  if (root === popup.root) { popup.root = null; popupStages = null; }
  const dialog = root.firstElementChild;
  animate(root, 'fade-out');
  animate(dialog, dialog.classList.contains('h-full') ? 'slide-out' : 'zoom-out');
  setTimeout(() => root.remove(), 200);
}
function closeAllModals() { while (openLayers().length) closeModal(); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

// The same side panel edits a block, or (with `add`) fills in a new to-do before it's created.
async function editDrawer(id, isRoot = false, add = null) {
  const [{ block: b }, options] = await Promise.all([
    add ? { block: { title: '', notes: '', due_date: null, rules: [], children_total: 0, pomodoros: 0 } } : api('GET', `/api/blocks/${id}`),
    isRoot ? Promise.resolve([])
      : add ? api('GET', `/api/blocks/${add.parent.id}/rule-options?new_child=1`)
      : api('GET', `/api/blocks/${id}/rule-options`),
  ]);
  const laterStages = curStages().slice(1);
  const stageSelect = (name, sel) => `<select name="${name}" class="rounded-lg border border-lavender-200 bg-white py-1 pl-2 pr-7 text-sm">
    ${laterStages.map(s => `<option value="${s.key}" ${s.key === sel ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}</select>`;
  const siblings = options.filter(o => o.sibling), others = options.filter(o => !o.sibling);
  const optById = new Map(options.map(o => [o.id, o]));
  // A rule row names one or more blocks: it's met once all of them reach the phase.
  const chipsHTML = ids => ids.filter(i => optById.has(i)).map(i => `
    <span class="chip inline-flex items-center gap-1 rounded-full bg-white border border-lavender-200 pl-2 pr-1 py-0.5 text-xs text-slate-700 max-w-[12rem]">
      <span class="truncate">${esc(optById.get(i).title)}</span>
      <button type="button" data-unpick="${i}" class="rounded-full px-1 text-slate-400 hover:text-blush-700" aria-label="Remove ${esc(optById.get(i).title)}">✕</button>
    </span>`).join('');
  const blockSelect = ids => `
    <div class="picker relative min-w-0 flex-1 basis-40 flex flex-wrap items-center gap-1">
      <input type="hidden" name="dep" value="${ids.join(',')}">
      <span class="chips contents">${chipsHTML(ids)}</span>
      <button type="button" data-pick class="rounded-lg border border-dashed border-lavender-300 bg-white py-0.5 px-2 text-xs text-lavender-700 hover:border-lavender-400">
        ${ids.length ? '+ block' : 'Choose blocks…'}
      </button>
    </div>`;
  const ruleRow = r => `
    <div class="rule flex flex-wrap items-center gap-1.5 rounded-xl bg-lavender-50 border border-lavender-100 p-2 text-sm text-slate-600">
      <span>Move to</span>${stageSelect('target', r.target_stage)}
      <span>when</span>${blockSelect(r.deps)}
      <span data-verb>${r.deps.length > 1 ? 'all reach' : 'reaches'}</span>${stageSelect('required', r.required_stage)}
      <button type="button" data-remove class="ml-auto p-1 rounded-lg text-slate-400 hover:text-blush-700 hover:bg-blush-50" aria-label="Remove rule">✕</button>
    </div>`;
  // Saved rules with the same phases show as one row with several blocks.
  const ruleRows = rules => {
    const rows = new Map();
    for (const r of rules) {
      const k = `${r.target_stage}|${r.required_stage}`;
      if (!rows.has(k)) rows.set(k, { target_stage: r.target_stage, required_stage: r.required_stage, deps: [] });
      rows.get(k).deps.push(r.depends_on_id);
    }
    return [...rows.values()];
  };
  const setDeps = (picker, ids) => {
    picker.querySelector('[name=dep]').value = ids.join(',');
    picker.querySelector('.chips').innerHTML = chipsHTML(ids);
    picker.querySelector('[data-pick]').textContent = ids.length ? '+ block' : 'Choose blocks…';
    const verb = picker.closest('.rule')?.querySelector('[data-verb]');
    if (verb) verb.textContent = ids.length > 1 ? 'all reach' : 'reaches';
  };
  const readRules = root => [...root.querySelectorAll('.rule')].flatMap(row =>
    row.querySelector('[name=dep]').value.split(',').filter(Boolean).map(d => ({
      depends_on_id: Number(d),
      target_stage: row.querySelector('[name=target]').value,
      required_stage: row.querySelector('[name=required]').value,
    })));
  const ruleKey = rules => JSON.stringify(rules.map(r => [r.depends_on_id, r.target_stage, r.required_stage]).sort());

  openModal(`
    <div class="flex items-center justify-between mb-5">
      <div>
        <h2 class="text-lg font-semibold">${add ? 'Add to-do' : 'Edit block'}</h2>
        ${add ? `<p class="text-sm text-slate-500">Inside “${esc(add.parent.title)}” · <span class="rounded-full px-2 py-0.5 text-xs font-semibold ${STAGE_STYLE[add.status].pill}">${esc(stageLabel(add.status))}</span></p>` : ''}
      </div>
      <button data-close class="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="Close">✕</button>
    </div>
    <label class="block text-sm font-medium text-slate-700 mb-1">Title</label>
    <input id="ed-title" value="${esc(b.title)}" autocomplete="off" ${add ? 'placeholder="e.g. Idle animation"' : ''} class="w-full rounded-lg border border-slate-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300">
    <label class="block text-sm font-medium text-slate-700 mt-4 mb-1">Description <span class="font-normal text-slate-400">(optional)</span></label>
    <textarea id="ed-notes" rows="4" class="w-full rounded-lg border border-slate-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300" placeholder="Links, acceptance criteria, anything useful">${esc(b.notes)}</textarea>
    <label class="block text-sm font-medium text-slate-700 mt-4 mb-1">Due date <span class="font-normal text-slate-400">(time optional, e.g. for appointments)</span></label>
    <div class="flex flex-wrap items-center gap-2">
      <input id="ed-due" type="date" value="${b.due_date || ''}" class="rounded-lg border border-slate-300 px-3 py-2">
      <input id="ed-time" type="time" value="${b.due_time || ''}" class="rounded-lg border border-slate-300 px-3 py-2">
      <button id="ed-due-clear" class="text-sm text-slate-500 hover:text-slate-800">Clear</button>
    </div>
    <div id="ed-remind" class="mt-2 ${b.due_date ? '' : 'hidden'}">
      <p class="text-xs font-semibold text-slate-600 mb-1">⏰ Remind me in the chat</p>
      <div class="flex flex-wrap gap-1.5">
        ${Object.entries(REMINDER_CHOICES).map(([k, l]) => `
          <label class="remind-opt inline-flex items-center gap-1.5 rounded-full border border-butter-200 bg-butter-50 px-2.5 py-1 text-xs text-butter-700 cursor-pointer" data-needs-time="${k === '1h' || k === '15m'}">
            <input type="checkbox" value="${k}" ${(b.remind ?? REMINDER_DEFAULT).includes(k) ? 'checked' : ''} class="rounded text-butter-700">${l}</label>`).join('')}
      </div>
    </div>
    ${isRoot ? '' : `
    <label class="block text-sm font-medium text-slate-700 mt-4 mb-1" for="ed-repeat">🔁 Repeats <span class="font-normal text-slate-400">(once it’s done, it comes back to “${esc(stageLabel(firstKey()))}” with its next due date)</span></label>
    <div class="flex flex-wrap items-center gap-2">
      <select id="ed-repeat" class="rounded-lg border border-slate-300 bg-white py-2 pl-3 pr-8 text-sm">
        ${REPEAT_PRESETS.map(([k, l]) => `<option value="${k}" ${repeatPreset(b.repeat) === k ? 'selected' : ''}>${l}</option>`).join('')}
      </select>
      <span id="ed-rep-custom" class="hidden items-center gap-1.5 text-sm text-slate-600">every
        <input id="ed-rep-n" type="number" min="1" max="365" value="${b.repeat?.interval || 1}" class="w-16 rounded-lg border border-slate-300 px-2 py-1.5" aria-label="How many">
        <select id="ed-rep-unit" class="rounded-lg border border-slate-300 bg-white py-1.5 pl-2 pr-7 text-sm" aria-label="Unit">
          ${[['daily', 'day(s)'], ['weekly', 'week(s)'], ['monthly', 'month(s)'], ['yearly', 'year(s)']].map(([k, l]) => `<option value="${k}" ${(b.repeat?.freq || 'weekly') === k ? 'selected' : ''}>${l}</option>`).join('')}
        </select></span>
    </div>
    <div id="ed-rep-days" class="hidden mt-2 flex flex-wrap gap-1">
      ${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d, i) => `<label class="inline-flex items-center gap-1 rounded-full border border-lavender-200 bg-lavender-50 px-2 py-0.5 text-xs text-lavender-700 cursor-pointer">
        <input type="checkbox" value="${i}" ${(b.repeat?.weekdays || []).includes(i) ? 'checked' : ''} class="rounded text-lavender-700">${d}</label>`).join('')}
    </div>
    ${b.repeat_next ? `<p class="mt-1 text-xs text-mint-700">Done for now: back on ${esc(fmtDay(b.repeat_next.reopen))}${b.repeat_next.due ? `, due ${esc(fmtDay(b.repeat_next.due))}` : ''}.</p>` : ''}
    ${b.children_total ? '' : `
    <label class="block text-sm font-medium text-slate-700 mt-4 mb-1" for="ed-est">⏳ Estimate <span class="font-normal text-slate-400">(hours it should take; used for the project’s forecast)</span></label>
    <div class="flex flex-wrap items-center gap-2 text-sm text-slate-500">
      <input id="ed-est" type="number" min="0" max="1000" step="0.25" value="${b.estimate_min ? +(b.estimate_min / 60).toFixed(2) : ''}" placeholder="none" class="w-24 rounded-lg border border-slate-300 px-3 py-2"> h
      ${b.spent_min ? `<span>· ${esc(fmtMin(b.spent_min))} of focus spent on it${b.estimate_min && b.spent_min > b.estimate_min ? ' <span class="text-blush-700">(past the estimate)</span>' : ''}</span>` : ''}
    </div>
    <label class="block text-sm font-medium text-slate-700 mt-4 mb-1" for="ed-track-total">📊 Counter <span class="font-normal text-slate-400">(optional, e.g. 200 course videos: its progress bar, and its project’s, follow the count)</span></label>
    <div class="flex flex-wrap items-center gap-2 text-sm text-slate-500">
      <input id="ed-track-done" type="number" min="0" max="1000000" value="${b.track?.done ?? ''}" placeholder="0" aria-label="How many are done" class="w-24 rounded-lg border border-slate-300 px-3 py-2">
      of
      <input id="ed-track-total" type="number" min="0" max="1000000" value="${b.track?.total ?? ''}" placeholder="none" aria-label="Total" class="w-24 rounded-lg border border-slate-300 px-3 py-2">
      <input id="ed-track-unit" maxlength="24" value="${esc(b.track?.unit || '')}" placeholder="e.g. videos" aria-label="What's counted" class="w-32 rounded-lg border border-slate-300 px-3 py-2">
    </div>`}
    <div class="mt-4 flex flex-wrap gap-4">
      <label class="block"><span class="block text-sm font-medium text-slate-700 mb-1">Priority</span>
        <select id="ed-prio" class="rounded-lg border border-slate-300 bg-white py-2 pl-3 pr-8 text-sm">${PRIORITY.map(([k, l]) => `<option value="${k}" ${String(b.priority || 0) === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="block flex-1 min-w-[12rem]"><span class="block text-sm font-medium text-slate-700 mb-1">Tags <span class="font-normal text-slate-400">(comma-separated)</span></span>
        <input id="ed-tags" value="${esc((b.tags || []).join(', '))}" autocomplete="off" placeholder="e.g. uni, writing" class="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"></label>
    </div>
    <label class="block text-sm font-medium text-slate-700 mt-4 mb-1" for="ed-pomo">Daily pomodoros <span class="font-normal text-slate-400">(focus sessions a day; 0 = no daily plan)</span></label>
    <input id="ed-pomo" type="number" min="0" max="24" value="${b.pomodoros ?? 4}" class="w-24 rounded-lg border border-slate-300 px-3 py-2">
    <div class="mt-6">
      <h3 class="text-sm font-bold text-slate-700">⚡ Rules</h3>
      <p class="text-xs text-slate-500 mb-2">Decide up front when ${add ? 'this to-do' : `“${esc(b.title)}”`} moves forward. It moves by itself as soon as a rule is met, and it can't be moved past a rule that isn't met yet.</p>
      ${b.children_total ? `<div class="rounded-xl bg-mint-50 border border-mint-100 p-2 text-xs text-mint-700 mb-2">Always on for blocks with items inside: ${esc(builtInRules())}.</div>` : ''}
      <div id="ed-rules" class="space-y-2">${ruleRows(b.rules).map(ruleRow).join('')}</div>
      ${options.length
        ? `<button type="button" id="ed-add-rule" class="mt-2 rounded-full border border-dashed border-lavender-300 px-3 py-1.5 text-sm text-lavender-700 hover:bg-lavender-50">+ Add rule</button>`
        : '<p class="text-sm text-slate-400">Add more blocks to this project to write rules about them.</p>'}
    </div>`}
    ${isRoot ? `
    <div class="mt-6">
      <h3 class="text-sm font-bold text-slate-700">Project</h3>
      <div class="mt-2 flex flex-wrap gap-2">
        <button id="ed-archive" class="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50" title="Hide it from Workflow, the HUD, Today and reminders; nothing is deleted">${ico('archive')}${b.archived_at ? 'Unarchive' : 'Archive project'}</button>
        <a href="/api/blocks/${id}/export.json" download class="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50" title="Everything in it as a file you can import again (here or on another computer)">${ico('download')}Export (JSON)</a>
        <button id="ed-import" class="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50" title="Paste a Markdown list or headings (e.g. from Obsidian) to add them as to-dos">${ico('upload')}Import outline here…</button>
      </div>
    </div>` : ''}
    <div class="flex flex-wrap items-center justify-between gap-2 mt-6 pt-4 border-t border-slate-200">
      <div class="flex gap-2">
        ${add || isRoot ? '' : '<button id="ed-move" class="rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50" title="Put it inside another block">Move to…</button>'}
        ${add ? '' : isRoot ? '' : '<button id="ed-dup" class="rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50" title="Copy this block with everything inside it — handy as a template (e.g. a new character)">Duplicate</button>'}
        ${add ? '' : `<button id="ed-del" class="rounded-lg px-3 py-2 text-sm text-blush-700 hover:bg-blush-50">Delete${isRoot ? ' project' : ''}</button>`}
      </div>
      <div class="flex gap-2">
        <button data-close class="rounded-lg px-4 py-2 text-slate-600 hover:bg-slate-100">Cancel</button>
        <button id="ed-save" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">${add ? 'Add' : 'Save'}</button>
      </div>
    </div>`, root => {
    const $ = s => root.querySelector(s);
    let clearDue = false;
    // Reminder choices show once there's a date; "before" ones need a time.
    const syncRemind = () => {
      $('#ed-remind').classList.toggle('hidden', !$('#ed-due').value);
      root.querySelectorAll('.remind-opt').forEach(l => {
        const off = l.dataset.needsTime === 'true' && !$('#ed-time').value;
        l.classList.toggle('opacity-40', off);
        l.querySelector('input').disabled = off;
      });
    };
    $('#ed-due').addEventListener('input', syncRemind);
    $('#ed-time').addEventListener('input', syncRemind);
    syncRemind();
    $('#ed-due-clear').onclick = () => { $('#ed-due').value = ''; $('#ed-time').value = ''; clearDue = true; syncRemind(); };
    const readRemind = () => [...root.querySelectorAll('.remind-opt input:checked')].map(i => i.value);
    const remindChanged = () => JSON.stringify(readRemind()) !== JSON.stringify((b.remind ?? REMINDER_DEFAULT).filter(k => !['1h', '15m'].includes(k) || $('#ed-time').value));
    const rulesBox = $('#ed-rules');
    rulesBox?.addEventListener('click', e => {
      if (e.target.closest('[data-remove]')) { const row = e.target.closest('.rule'); animateOut([row], 'poof', 200).then(() => row.remove()); }
      else if (e.target.closest('[data-unpick]')) {
        const picker = e.target.closest('.picker'), gone = Number(e.target.closest('[data-unpick]').dataset.unpick);
        setDeps(picker, picker.querySelector('[name=dep]').value.split(',').filter(Boolean).map(Number).filter(i => i !== gone));
      }
      else if (e.target.closest('[data-pick]')) openBlockPicker(e.target.closest('.picker'), options, setDeps);
    });
    $('#ed-add-rule')?.addEventListener('click', () => {
      rulesBox.insertAdjacentHTML('beforeend', ruleRow({ deps: [], target_stage: curStages()[1].key, required_stage: lastKey() }));
      animate(rulesBox.lastElementChild, 'pop-in');
      openBlockPicker(rulesBox.lastElementChild.querySelector('.picker'), options, setDeps);
    });
    const readPomo = () => { const el = $('#ed-pomo'); return el ? Math.max(0, Math.min(24, Math.round(Number(el.value) || 0))) : null; };
    // Repeat: a preset, or "every n days/weeks/months/years" (with weekdays for weeks).
    const syncRepeat = () => {
      if (!$('#ed-repeat')) return;
      const custom = $('#ed-repeat').value === 'custom';
      $('#ed-rep-custom').classList.toggle('hidden', !custom);
      $('#ed-rep-custom').classList.toggle('inline-flex', custom);
      $('#ed-rep-days').classList.toggle('hidden', !(custom && $('#ed-rep-unit').value === 'weekly'));
    };
    $('#ed-repeat')?.addEventListener('change', syncRepeat);
    $('#ed-rep-unit')?.addEventListener('change', syncRepeat);
    syncRepeat();
    const readRepeat = () => {
      const v = $('#ed-repeat')?.value;
      if (!v || v === 'none') return null;
      if (v !== 'custom') return { ...REPEAT_RULES[v] };
      const freq = $('#ed-rep-unit').value, interval = Math.max(1, Math.min(365, Math.round(Number($('#ed-rep-n').value) || 1)));
      const days = [...root.querySelectorAll('#ed-rep-days input:checked')].map(i => Number(i.value));
      return { freq, interval, ...(freq === 'weekly' && days.length ? { weekdays: days } : {}) };
    };
    // Only what changed: { repeat } or { clear_repeat }, and { estimate_min }.
    const extraPatch = () => {
      const out = {}, rule = readRepeat();
      if (repeatKey(rule) !== repeatKey(b.repeat)) Object.assign(out, rule ? { repeat: rule } : { clear_repeat: true });
      const prio = $('#ed-prio');
      if (prio && Number(prio.value) !== (b.priority || 0)) out.priority = Number(prio.value);
      const tagsEl = $('#ed-tags');
      if (tagsEl) {
        const tags = tagsEl.value.split(',').map(t => t.trim().replace(/^#/, '')).filter(Boolean);
        if (JSON.stringify(tags) !== JSON.stringify(b.tags || [])) out.tags = tags;
      }
      const est = $('#ed-est');
      if (est) {
        const mins = est.value === '' ? 0 : Math.max(0, Math.round(Number(est.value) * 60) || 0);
        if (mins !== (b.estimate_min || 0)) out.estimate_min = mins;
      }
      const total = $('#ed-track-total');
      if (total) {
        const n = Math.max(0, Math.round(Number(total.value) || 0));
        const done = Math.max(0, Math.round(Number($('#ed-track-done').value) || 0)), unit = $('#ed-track-unit').value.trim();
        if (!n && b.track) out.track_total = 0;
        else if (n && (n !== b.track?.total || done !== b.track?.done || unit !== (b.track?.unit || '')))
          Object.assign(out, { track_total: n, track_done: Math.min(done, n), track_unit: unit });
      }
      return out;
    };
    $('#ed-save').onclick = async () => {
      const title = $('#ed-title').value.trim();
      if (!title) { $('#ed-title').focus(); return; }
      const due = $('#ed-due').value;
      if (add) {
        // Create it first, then apply its rules, then put it in the stage it was added to (rules permitting).
        const r = await attempt(() => api('POST', '/api/blocks', { title, notes: $('#ed-notes').value, parent_id: add.parent.id, status: firstKey() }));
        if (!r) return;
        const newId = r.block.id;
        if (due) await attempt(() => api('PATCH', `/api/blocks/${newId}`, {
          due_date: due, ...($('#ed-time').value ? { due_time: $('#ed-time').value } : {}),
          ...(remindChanged() ? { remind: readRemind() } : {}),
        }));
        if (readPomo() !== null && readPomo() !== 0) await attempt(() => api('PATCH', `/api/blocks/${newId}`, { pomodoros: readPomo() }));
        const extra = extraPatch();
        if (Object.keys(extra).length) await attempt(() => api('PATCH', `/api/blocks/${newId}`, extra));
        const rules = readRules(root);
        if (rules.length) await attempt(() => api('PUT', `/api/blocks/${newId}/rules`, { rules }));
        if (add.status !== firstKey()) await attempt(() => api('POST', `/api/blocks/${newId}/move`, { status: add.status }));
        closeModal(); refresh();
        return;
      }
      const patch = { title, notes: $('#ed-notes').value };
      if (due) patch.due_date = due; else if (clearDue || b.due_date) patch.clear_due_date = true;
      if (due && $('#ed-time').value) patch.due_time = $('#ed-time').value;
      else if (due && b.due_time) patch.clear_due_time = true;
      if (due && remindChanged()) patch.remind = readRemind();
      if (readPomo() !== null && readPomo() !== (b.pomodoros ?? 4)) patch.pomodoros = readPomo();
      Object.assign(patch, extraPatch());
      let ok = await attempt(() => api('PATCH', `/api/blocks/${id}`, patch));
      if (ok && !isRoot) {
        const rules = readRules(root);
        if (ruleKey(rules) !== ruleKey(b.rules)) ok = await attempt(() => api('PUT', `/api/blocks/${id}/rules`, { rules }));
      }
      if (ok) { closeModal(); refresh(); }
    };
    $('#ed-dup')?.addEventListener('click', async () => {
      const r = await attempt(() => api('POST', `/api/blocks/${id}/duplicate`));
      if (r) { closeModal(); toast(`Created “${r.block.title}”. Rename it from its ⋯ menu.`, 'success'); refresh(); }
    });
    if (add) $('#ed-title').focus();
    $('#ed-move')?.addEventListener('click', () => moveToPicker(id, b.title));
    $('#ed-archive')?.addEventListener('click', async () => { if (await setArchived(b, !b.archived_at)) closeModal(); });
    $('#ed-import')?.addEventListener('click', () => importModal({ parentId: id, parentTitle: b.title }));
    $('#ed-del')?.addEventListener('click', async () => {
      const inside = b.children_total ? ` and the ${b.leaves_total} item(s) nested inside it` : '';
      if (!confirm(`Delete “${b.title}”${inside}? It goes to the trash (Settings → 🗑 Trash & backups) for 30 days.`)) return;
      const r = await attempt(() => api('DELETE', `/api/blocks/${id}`));
      if (!r) return;
      deletedToast(b.title, r.trash_id);
      closeModal();
      await animateOut(document.querySelectorAll(`.card[data-id="${id}"], [data-go="${id}"]`), 'poof', 220);
      const current = state.data?.block.id;
      if (current === id) go(isRoot ? null : b.parent_id); else refresh();
    });
  }, { drawer: true, stack: true });
}

// Searchable block chooser for rules: type to filter by name or location, optionally hide finished blocks.
// Clicking a block adds or removes it from the rule (several blocks = all of them must get there).
function openBlockPicker(picker, options, setDeps) {
  closeBlockPicker();
  const hidden = picker.querySelector('[name=dep]');
  const button = picker.querySelector('[data-pick]');
  const picked = () => hidden.value.split(',').filter(Boolean).map(Number);
  const siblings = options.filter(o => o.sibling);
  const pop = document.createElement('div');
  pop.className = 'block-picker absolute z-50 left-0 top-full mt-1 w-[min(22rem,80vw)] rounded-2xl border border-lavender-200 bg-white shadow-lg p-2';
  pop.innerHTML = `
    <input type="search" placeholder="Search blocks…" autocomplete="off" class="w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-lavender-300">
    <label class="flex items-center gap-1.5 px-1 pt-1.5 pb-1 text-xs text-slate-500">
      <input type="checkbox" data-hide-done class="rounded border-slate-300 text-lavender-700"> Hide completed
      ${siblings.length > 1 ? `<button type="button" data-all class="ml-auto rounded-full border border-lavender-200 px-2 py-0.5 text-lavender-700 hover:bg-lavender-50" title="Pick every block next to this one">All next to it (${siblings.length})</button>` : ''}
    </label>
    <div class="list max-h-64 overflow-y-auto scroll-thin" role="listbox"></div>`;
  picker.appendChild(pop);
  animate(pop, 'drop-in');
  const search = pop.querySelector('input[type=search]'), hideDone = pop.querySelector('[data-hide-done]');
  const list = pop.querySelector('.list');
  hideDone.checked = localGet('nestwork.hideDone') !== '0';
  let matches = [], active = 0;

  const draw = () => {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    const sel = new Set(picked());
    matches = options.filter(o =>
      (!hideDone.checked || !o.done || sel.has(o.id)) &&
      words.every(w => `${o.path} ${o.title}`.toLowerCase().includes(w)));
    active = Math.min(active, Math.max(matches.length - 1, 0));
    const hiddenCount = hideDone.checked ? options.filter(o => o.done && !sel.has(o.id)).length : 0;
    let lastGroup = null;
    list.innerHTML = matches.map((o, i) => {
      const group = o.sibling ? 'Next to it' : 'Elsewhere in the project';
      const head = group !== lastGroup ? `<p class="px-2 pt-2 pb-0.5 text-[11px] font-bold uppercase tracking-wider text-slate-400">${group}</p>` : '';
      lastGroup = group;
      return `${head}<button type="button" data-i="${i}" role="option" class="w-full text-left flex items-start gap-2 rounded-lg px-2 py-1.5 ${i === active ? 'bg-lavender-50' : 'hover:bg-slate-50'}">
        <span class="mt-0.5 w-4 shrink-0 text-center text-lavender-700 text-sm">${sel.has(o.id) ? '✓' : ''}</span>
        <span class="mt-1.5 w-2 h-2 rounded-full shrink-0 ${STAGE_STYLE[o.status].dot}"></span>
        <span class="min-w-0 flex-1">
          <span class="block text-sm truncate ${sel.has(o.id) ? 'font-bold text-lavender-800' : 'text-slate-700'}">${esc(o.title)}</span>
          ${o.path ? `<span class="block text-xs text-slate-400 truncate">${esc(o.path)}</span>` : ''}
        </span>
        <span class="shrink-0 rounded-full px-1.5 text-[11px] ${STAGE_STYLE[o.status].pill}">${esc(stageLabel(o.status))}</span>
      </button>`;
    }).join('') || `<p class="px-2 py-3 text-sm text-slate-400">No blocks match.</p>`;
    if (hiddenCount) list.insertAdjacentHTML('beforeend', `<p class="px-2 pt-1.5 text-xs text-slate-400">${hiddenCount} completed hidden</p>`);
    list.querySelector(`[data-i="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  };
  const choose = o => {
    const ids = picked();
    setDeps(picker, ids.includes(o.id) ? ids.filter(i => i !== o.id) : [...ids, o.id]);
    draw();
    search.focus();
  };
  pop.querySelector('[data-all]')?.addEventListener('click', () => {
    setDeps(picker, [...new Set([...picked(), ...siblings.map(o => o.id)])]);
    draw();
    search.focus();
  });

  search.addEventListener('input', () => { active = 0; draw(); });
  hideDone.addEventListener('change', () => { localSet('nestwork.hideDone', hideDone.checked ? '1' : '0'); draw(); search.focus(); });
  list.addEventListener('mousedown', e => e.preventDefault());   // keep focus in the search box
  list.addEventListener('click', e => { const b = e.target.closest('[data-i]'); if (b) choose(matches[Number(b.dataset.i)]); });
  search.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      active = Math.max(0, Math.min(matches.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)));
      draw();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (matches[active]) choose(matches[active]);
    } else if (e.key === 'Escape') {
      e.stopPropagation();   // close just the picker, not the whole panel
      closeBlockPicker();
      button.focus();
    }
  });
  setTimeout(() => document.addEventListener('mousedown', outsideClose), 0);
  draw();
  search.focus();
}

function outsideClose(e) {
  if (!e.target.closest('.block-picker') && !e.target.closest('[data-pick]')) closeBlockPicker();
}

function closeBlockPicker() {
  document.querySelector('.block-picker')?.remove();
  document.removeEventListener('mousedown', outsideClose);
}

// Plain-language version of the built-in container rules for this project's phases.
function builtInRules() {
  const st = curStages();
  const active = st.find(s => s.role === 'active'), review = st.find(s => s.role === 'review') || st[st.length - 1];
  return [active && `moves to “${active.label}” when work starts inside`,
          `follows its slowest to-do (it moves to the furthest phase everything inside has reached)`,
          `waits in “${review.label}” for your OK once everything inside is complete`].filter(Boolean).join('; ');
}

// Phase editor: rename, recolor, reorder, add and remove the project's phases, and pick which
// phases carry the built-in "work in progress" and "review" behaviour.
// ---------------------------------------------------------------- approving finished bubbles
// When everything inside a bubble is complete, it waits for your OK. A popup asks to move it to the
// last phase; bubbles around it that would then be complete too are offered in the same popup.
const approvals = { dismissed: new Set(), open: false };

function askApprovals(data) {
  if (approvals.open || openLayers().length) return;   // don't interrupt; the next refresh asks again
  const last = lastKey();
  const cards = { ...(data.cards || {}) };
  if (!data.is_root) cards[data.block.id] = data.block;
  const nodes = {};
  const index = n => { nodes[n.id] = n; n.children.forEach(index); };
  index(data.tree);
  const done = id => cards[id]?.status === last;
  const waiting = Object.values(cards).filter(c => c.children_total && c.status !== last
    && (nodes[c.id]?.children || []).every(k => done(k.id)) && !approvals.dismissed.has(c.id));
  if (!waiting.length) return;
  // Bubbles around them that would be complete once these are accepted.
  const chosen = new Set(waiting.map(c => c.id));
  for (let grew = true; grew;) {
    grew = false;
    for (const c of Object.values(cards)) {
      if (!c.children_total || done(c.id) || chosen.has(c.id) || approvals.dismissed.has(c.id)) continue;
      const kids = nodes[c.id]?.children || [];
      if (kids.length && kids.every(k => done(k.id) || chosen.has(k.id))) { chosen.add(c.id); grew = true; }
    }
  }
  const depth = id => { let d = 0, c = cards[id]; while (c && cards[c.parent_id]) { d++; c = cards[c.parent_id]; } return d; };
  const list = [...chosen].map(id => cards[id]).sort((a, b) => depth(b.id) - depth(a.id));   // innermost first
  const lastLabel = stageLabel(last);
  approvals.open = true;
  openModal(`
    <h2 class="text-lg font-bold">✓ Ready to approve</h2>
    <p class="text-sm text-slate-500 mt-1 mb-4">Everything inside ${list.length > 1 ? 'these is' : 'this is'} complete. Move ${list.length > 1 ? 'them' : 'it'} to “${esc(lastLabel)}”?</p>
    <div class="space-y-2">
      ${list.map(c => `
        <label class="flex items-start gap-3 rounded-2xl border border-mint-200 bg-mint-50 p-3 cursor-pointer">
          <input type="checkbox" value="${c.id}" checked class="mt-1 rounded text-mint-700">
          <span class="min-w-0"><span class="block font-semibold text-slate-800">${esc(c.title)}</span>
          <span class="block text-xs text-slate-500">${c.leaves_total} task${c.leaves_total === 1 ? '' : 's'} inside, all complete${waiting.some(w => w.id === c.id) ? '' : ' (once the ones above are approved)'}</span></span>
        </label>`).join('')}
    </div>
    <div class="flex justify-end gap-2 mt-6">
      <button data-later class="rounded-full px-4 py-2 text-slate-600 hover:bg-slate-100">Not yet</button>
      <button data-approve class="rounded-full bg-mint-300 hover:bg-mint-200 text-slate-900 font-semibold px-5 py-2">Approve</button>
    </div>`, root => {
    const finish = () => { approvals.open = false; closeModal(); };
    root.querySelector('[data-later]').onclick = () => { list.forEach(c => approvals.dismissed.add(c.id)); finish(); };
    root.addEventListener('mousedown', e => { if (e.target === root) list.forEach(c => approvals.dismissed.add(c.id)); });
    root.querySelector('[data-approve]').onclick = async e => {
      e.currentTarget.disabled = true;
      const ids = [...root.querySelectorAll('input:checked')].map(i => Number(i.value));
      list.filter(c => !ids.includes(c.id)).forEach(c => approvals.dismissed.add(c.id));
      for (const c of list.filter(c => ids.includes(c.id))) {   // innermost first, so the outer ones can follow
        const r = await attempt(() => api('POST', `/api/blocks/${c.id}/move`, { status: last }));
        if (!r) break;
      }
      finish();
      refresh();
    };
  });
  // Closing it any other way (✕, Esc, clicking outside) counts as "not yet".
  const layer = openLayers().pop();
  new MutationObserver((_, obs) => {
    if (!document.body.contains(layer) || layer.classList.contains('closing')) {
      if (approvals.open) list.forEach(c => approvals.dismissed.add(c.id));
      approvals.open = false; obs.disconnect();
    }
  }).observe(modalRoot, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
}

// ---------------------------------------------------------------- block popup
// Everything about a block without leaving the board: description, phase, rules, its own nested
// board, adding to-dos inside it, moving it. Edit panels open on top of it.
const popup = { id: null, root: null };

async function openBlockPopup(id) {
  let data;
  try { data = await api('GET', `/api/blocks/${id}`); } catch (e) { return toast(e.message, 'error'); }
  if (popup.root && document.body.contains(popup.root)) {
    popup.id = id;
    drawPopup(data);
    animate(popup.root.firstElementChild, 'page-side');
    return;
  }
  openModal('<div class="pp"></div>', root => { popup.root = root; popup.id = id; drawPopup(data, true); }, { xl: true, stack: true });
}

async function refreshPopup() {
  if (!popup.root || !document.body.contains(popup.root)) return;
  try { drawPopup(await api('GET', `/api/blocks/${popup.id}`)); }
  catch { const r = popup.root; popup.root = null; popupStages = null; r.remove(); syncDrawerClass(); }   // it was deleted
}

// The nearest ancestor that scrolls (to keep its position across a redraw).
function scrollParent(el) {
  for (let e = el; e && e !== document.body; e = e.parentElement) {
    const o = getComputedStyle(e).overflowY;
    if ((o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight) return e;
  }
  return null;
}

function drawPopup(data, first = false) {
  popupStages = state.data?.root_id === data.root_id ? null : data.stages;   // a to-do from another project (or no board open)
  registerStages(data.stages);
  const b = data.block, box = popup.root.querySelector('.pp');
  const crumbs = data.breadcrumb;
  const scroller = first ? null : scrollParent(box), top = scroller?.scrollTop;
  box.innerHTML = `
    <div class="pp-head flex items-start justify-between gap-3">
      <nav class="flex flex-wrap items-center gap-1 text-xs text-slate-500 min-w-0">
        ${crumbs.map((c, i) => i === 0 ? `<span>${esc(c.title)}</span>` : `<span class="text-slate-300">/</span><button data-crumb="${c.id}" class="hover:text-lavender-700 hover:underline">${esc(c.title)}</button>`).join('')}
      </nav>
      <div class="flex items-center gap-1.5 shrink-0">
        <button data-pp="edit" class="rounded-full border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50" title="Details, due date, reminders, rules">Edit details</button>
        <button data-pp="move" class="rounded-full border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50">Move to…</button>
        <a href="#/b/${b.id}" class="rounded-full border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50" title="Open it as its own page">Open as page ↗</a>
        <button data-close class="p-1 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="Close">✕</button>
      </div>
    </div>
    <input class="pp-title mt-1 w-full bg-transparent text-2xl font-extrabold text-slate-800 rounded-lg px-1 -mx-1 hover:bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-lavender-300" value="${esc(b.title)}" aria-label="Title">
    <div class="mt-1 flex flex-wrap items-center gap-2">${dueBadge(b.due_date, b.done, b.due_time)}${priorityBadge(b)}${repeatBadge(b)}${estimateBadge(b)}${tagChips(b)}
      ${b.children_total ? `<span class="flex items-center gap-1.5 w-40">${bar(b.progress, 'h-1.5')}<span class="text-xs text-slate-500">${pct(b.progress)}%</span></span>
        <span class="text-xs text-slate-500">${b.leaves_done} of ${b.leaves_total} tasks done</span>` : ''}</div>
    <div class="pp-track">${b.children_total || !b.parent_id ? '' : b.track ? trackHTML(b, true) : trackSetupHTML(b)}</div>
    <label class="block mt-3"><span class="text-[11px] font-bold uppercase tracking-wider text-slate-400">Description</span>
      <textarea class="pp-desc mt-1 block w-full resize-none overflow-hidden rounded-2xl border border-transparent bg-lavender-50/50 hover:bg-lavender-50 focus:bg-white focus:border-lavender-200 px-3 py-2 text-slate-700 placeholder-slate-400 focus:outline-none" rows="2" placeholder="What is this about? Details, links, what “done” looks like…">${esc(b.notes)}</textarea>
      <span class="pp-saved block h-4 text-xs text-slate-400"></span></label>
    ${attachmentsPanelHTML(b)}
    ${stagePicker(b)}
    <div class="mt-3 space-y-2 text-sm">
      ${forecastHTML(data.forecast)}
      ${b.warning ? `<div class="rounded-xl bg-blush-50 border border-blush-200 px-3 py-2 text-blush-700">⚠️ Further along than its rules allow. Still waiting until ${esc(b.warning_reasons.join(' and '))}.</div>` : ''}
      ${b.locked && !b.warning ? `<div class="flex items-start gap-2 rounded-xl bg-butter-50 border border-butter-200 px-3 py-2 text-butter-700">${ICON.lock}<span>Can't start until ${esc(b.blocked_by.map(u => u.text).join(' and '))}.</span></div>` : ''}
      ${!b.locked && b.gate && !b.done ? `<div class="rounded-xl bg-lavender-50 border border-lavender-100 px-3 py-2 text-lavender-700">→ Can move to “${esc(stageLabel(b.gate.stage))}” when ${esc(b.gate.reasons.join(' and '))}.</div>` : ''}
      ${b.automation.length ? `<div class="rounded-xl bg-mint-50 border border-mint-100 px-3 py-2 text-mint-700"><span class="font-bold">⚡ Automatic:</span> ${esc(b.automation.join(' · '))}</div>` : ''}
    </div>
    <div class="mt-5">${notesPanelHTML(b)}</div>
    <div class="mt-5 flex items-center justify-between">
      <h3 class="text-sm font-bold text-slate-700">${b.children_total ? `Inside “${esc(b.title)}”` : 'To-do list'}</h3>
      <button data-pp="add" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-semibold px-3 py-1.5">+ Add a to-do inside</button>
    </div>
    ${b.children_total ? '<div class="pp-board mt-2"></div>'
      : `<p class="mt-2 rounded-2xl border-2 border-dashed border-lavender-200 p-4 text-center text-sm text-slate-400">“${esc(b.title)}” is a single task. Add to-dos inside to break it down.</p>`}`;
  popup.root.querySelectorAll('[data-close]').forEach(x => x.onclick = closeModal);
  if (b.children_total) {
    renderBoard(box.querySelector('.pp-board'), data, { popup: true });
    if (first) stagger(box.querySelectorAll('.card'), 'pop-in', 25, 300);
  }
  bindNotes(box, b);
  bindAttachments(box, b);
  bindTrack(box.querySelector('.pp-track'));   // only its own counter: the board inside binds its cards
  if (scroller) scroller.scrollTop = top;
  box.querySelectorAll('[data-crumb]').forEach(c => c.onclick = () => openBlockPopup(Number(c.dataset.crumb)));
  box.querySelector('[data-pp="edit"]').onclick = () => editDrawer(b.id);
  box.querySelector('[data-pp="move"]').onclick = () => moveToPicker(b.id, b.title);
  box.querySelector('[data-pp="add"]').onclick = () => newTodoModal({ parentId: b.id, parentTitle: b.title });
  box.querySelectorAll('[data-stage]').forEach(btn => btn.onclick = async () => {
    if (btn.dataset.stage !== b.status && await attempt(() => api('POST', `/api/blocks/${b.id}/move`, { status: btn.dataset.stage }))) refresh();
  });
  // Title and description save themselves.
  const title = box.querySelector('.pp-title');
  title.onchange = async () => {
    if (!title.value.trim()) { title.value = b.title; return; }
    if (await attempt(() => api('PATCH', `/api/blocks/${b.id}`, { title: title.value.trim() }))) { b.title = title.value.trim(); renderCurrent(); }
  };
  title.onkeydown = e => { if (e.key === 'Enter') title.blur(); };
  const desc = box.querySelector('.pp-desc'), saved = box.querySelector('.pp-saved');
  const grow = () => { desc.style.height = 'auto'; desc.style.height = desc.scrollHeight + 'px'; };
  let timer = null, last = b.notes;
  const save = async () => {
    clearTimeout(timer);
    if (desc.value === last) return;
    const value = desc.value;
    saved.textContent = 'Saving…';
    try { await api('PATCH', `/api/blocks/${b.id}`, { notes: value }); last = value; saved.textContent = 'Saved ✓'; animate(saved, 'fade-in'); renderCurrent(); }
    catch (e) { saved.textContent = ''; toast(e.message, 'error'); }
  };
  desc.addEventListener('input', () => { grow(); clearTimeout(timer); timer = setTimeout(save, 700); });
  desc.addEventListener('blur', save);
  requestAnimationFrame(grow);
}

// Pick a block to move this one into (same project; not itself or anything inside it).
async function moveToPicker(id, title) {
  let targets;
  try { targets = await api('GET', `/api/blocks/${id}/move-targets`); } catch (e) { return toast(e.message, 'error'); }
  openModal(`
    <div class="flex items-center justify-between mb-1">
      <h2 class="text-lg font-bold">Move “${esc(title)}”</h2>
      <button data-close class="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="Close">✕</button>
    </div>
    <p class="text-sm text-slate-500 mb-3">Put it, with everything inside it, into another block. Its rules come along.</p>
    <input class="mv-search w-full rounded-xl border border-slate-300 px-3 py-2 text-sm mb-2 focus:outline-none focus:ring-2 focus:ring-lavender-300" placeholder="Search blocks…">
    <ul class="mv-list max-h-[50vh] overflow-y-auto scroll-thin space-y-0.5">
      ${targets.map(t => `<li><button data-target="${t.id}" ${t.current ? 'disabled' : ''} data-search="${esc((t.path + ' ' + t.title).toLowerCase())}"
        class="w-full text-left rounded-lg px-2 py-1.5 text-sm ${t.current ? 'text-slate-400 cursor-default' : 'hover:bg-lavender-50'}" style="padding-left:${0.5 + t.depth * 1.1}rem">
        ${t.is_root ? '🏠 ' : ''}<span class="${t.done ? 'line-through text-slate-400' : 'text-slate-700'} ${t.is_root ? 'font-semibold' : ''}">${esc(t.is_root ? `${t.title} (top level)` : t.title)}</span>
        ${t.current ? '<span class="ml-1 text-xs text-slate-400">(where it is now)</span>' : ''}</button></li>`).join('')}
    </ul>`, root => {
    const search = root.querySelector('.mv-search');
    search.focus();
    search.oninput = () => {
      const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
      root.querySelectorAll('[data-target]').forEach(b => b.parentElement.classList.toggle('hidden', !words.every(w => b.dataset.search.includes(w))));
    };
    root.querySelector('.mv-list').onclick = async e => {
      const t = e.target.closest('[data-target]');
      if (!t || t.disabled) return;
      const r = await attempt(() => api('POST', `/api/blocks/${id}/move-into`, { parent_id: Number(t.dataset.target) }));
      if (!r) return;
      closeModal();
      refresh();
    };
  }, { stack: true });
}

function stagesModal() {
  const data = state.data;
  const project = data.breadcrumb[0]?.title ?? data.block.title;
  let rows = data.stages.map(s => ({ key: s.key, label: s.label, color: s.color, role: ['active', 'review'].includes(s.role) ? s.role : '' }));
  const roleHelp = {
    active: 'Where work happens: to-dos here count as “already started” in Today.',
    review: 'When everything inside a block is complete, the block waits here for your OK.',
  };

  openModal(`
    <h2 class="text-lg font-bold">Phases</h2>
    <p class="text-sm text-slate-500 mt-1">The columns every block in “${esc(project)}” moves through, from first to last. Rename, recolor, reorder, add or remove them.</p>
    <div id="ph-list" class="mt-4 space-y-2"></div>
    <button type="button" id="ph-add" class="mt-2 rounded-full border border-dashed border-lavender-300 px-3 py-1.5 text-sm text-lavender-700 hover:bg-lavender-50">+ Add phase</button>
    <div class="mt-4 rounded-xl bg-slate-50 border border-slate-200 p-3 text-xs text-slate-500 space-y-1">
      <p><b class="text-slate-600">First phase:</b> where new to-dos start and where waiting ones stay.</p>
      <p><b class="text-slate-600">Last phase:</b> done. Progress counts what’s here.</p>
      <p><b class="text-slate-600">⚙ Work in progress:</b> ${roleHelp.active}</p>
      <p><b class="text-slate-600">🔍 Review:</b> ${roleHelp.review} Without one, it completes by itself.</p>
      <p><b class="text-slate-600">To-dos with to-dos inside</b> are bubbles: each sits in the phase of its slowest to-do and stretches to its farthest one.</p>
      <p>To-dos in a removed phase move to the phase before it.</p>
    </div>
    <div class="flex justify-end gap-2 mt-6">
      <button data-close class="rounded-full px-4 py-2 text-slate-600 hover:bg-slate-100">Cancel</button>
      <button id="ph-save" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">Save phases</button>
    </div>`, root => {
    const list = root.querySelector('#ph-list');
    const draw = (animateKey = null) => {
      list.innerHTML = rows.map((r, i) => {
        const first = i === 0, last = i === rows.length - 1, c = r.color || PALETTE[i % PALETTE.length];
        return `
        <div class="ph-row flex flex-wrap items-center gap-2 rounded-2xl border border-${c}-200 bg-${c}-50 p-2" data-i="${i}">
          <button type="button" data-color title="Change color" class="w-6 h-6 shrink-0 rounded-full bg-${c}-300 ring-2 ring-white/70"></button>
          <input data-label value="${esc(r.label)}" maxlength="40" placeholder="Phase name" class="min-w-0 flex-1 basis-32 rounded-lg border border-${c}-200 bg-white px-2.5 py-1.5 text-sm">
          ${first ? `<span class="text-xs font-semibold text-${c}-700 px-2">Start</span>`
            : last ? `<span class="text-xs font-semibold text-${c}-700 px-2">Done ✓</span>`
            : `<select data-role class="rounded-lg border border-${c}-200 bg-white py-1 pl-2 pr-7 text-xs" title="Built-in behaviour">
                 <option value="" ${!r.role ? 'selected' : ''}>No role</option>
                 <option value="active" ${r.role === 'active' ? 'selected' : ''}>⚙ Work in progress</option>
                 <option value="review" ${r.role === 'review' ? 'selected' : ''}>🔍 Review</option>
               </select>`}
          <div class="flex items-center ml-auto">
            <button type="button" data-up ${first ? 'disabled' : ''} class="p-1 rounded-lg text-slate-500 hover:bg-white/70 disabled:opacity-30" aria-label="Move up">↑</button>
            <button type="button" data-down ${last ? 'disabled' : ''} class="p-1 rounded-lg text-slate-500 hover:bg-white/70 disabled:opacity-30" aria-label="Move down">↓</button>
            <button type="button" data-del ${rows.length <= 2 ? 'disabled' : ''} class="p-1 rounded-lg text-slate-400 hover:text-blush-700 hover:bg-blush-50 disabled:opacity-30" aria-label="Remove phase">✕</button>
          </div>
        </div>`;
      }).join('');
      if (animateKey != null) animate(list.children[animateKey], 'pop-in');
    };
    const sync = () => list.querySelectorAll('.ph-row').forEach(el => {
      const r = rows[Number(el.dataset.i)];
      r.label = el.querySelector('[data-label]').value;
    });
    list.addEventListener('click', e => {
      const row = e.target.closest('.ph-row'); if (!row) return;
      sync();
      const i = Number(row.dataset.i);
      if (e.target.closest('[data-color]')) {
        const cur = rows[i].color || PALETTE[i % PALETTE.length];
        rows[i].color = PALETTE[(PALETTE.indexOf(cur) + 1) % PALETTE.length];
        draw();
      } else if (e.target.closest('[data-up]') && i > 0) {
        [rows[i - 1], rows[i]] = [rows[i], rows[i - 1]]; draw(i - 1);
      } else if (e.target.closest('[data-down]') && i < rows.length - 1) {
        [rows[i + 1], rows[i]] = [rows[i], rows[i + 1]]; draw(i + 1);
      } else if (e.target.closest('[data-del]') && rows.length > 2) {
        animateOut([row], 'poof', 200).then(() => { rows.splice(i, 1); draw(); });
      }
    });
    list.addEventListener('change', e => {
      if (!e.target.matches('[data-role]')) return;
      sync();
      const i = Number(e.target.closest('.ph-row').dataset.i);
      rows.forEach((r, j) => { if (j !== i && r.role === e.target.value) r.role = ''; });   // one phase per role
      rows[i].role = e.target.value;
      draw();
    });
    root.querySelector('#ph-add').onclick = () => {
      sync();
      if (rows.length >= 10) { toast('A project can have up to 10 phases.', 'error'); return; }
      rows.splice(rows.length - 1, 0, { key: null, label: '', color: PALETTE[(rows.length - 1) % PALETTE.length], role: '' });
      draw(rows.length - 2);
      list.children[rows.length - 2].querySelector('[data-label]').focus();
    };
    root.querySelector('#ph-save').onclick = async () => {
      sync();
      const empty = rows.findIndex(r => !r.label.trim());
      if (empty >= 0) { list.children[empty].querySelector('[data-label]').focus(); toast('Give every phase a name.', 'error'); return; }
      const removed = data.stages.filter(s => !rows.some(r => r.key === s.key));
      if (removed.length && !confirm(`Remove ${removed.map(s => `“${s.label}”`).join(', ')}? To-dos there move to the phase before it.`)) return;
      const stages = rows.map((r, i) => ({ key: r.key || undefined, label: r.label.trim(), color: r.color,
        role: i > 0 && i < rows.length - 1 && r.role ? r.role : null }));
      const res = await attempt(() => api('PUT', `/api/blocks/${data.block.id}/stages`, { stages }));
      if (res) { closeModal(); refresh(); }
    };
    draw();
  });
}

// ---------------------------------------------------------------- archive, import
async function setArchived(b, archived) {
  const r = await attempt(() => api('PATCH', `/api/blocks/${b.id}`, { archived }));
  if (!r) return false;
  toast(archived ? `Archived “${b.title}”. Find it under Workflow → Archive; its dates stay on the calendar.` : `“${b.title}” is back in Workflow.`, 'success',
    archived ? { label: 'Undo', run: () => setArchived(b, false) } : null);
  refresh();
  return true;
}

function archivedModal(list) {
  openModal(`
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-lg font-semibold">Archived projects</h2>
      <button data-close class="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="Close">✕</button>
    </div>
    <p class="text-sm text-slate-500">Hidden from Workflow, the HUD, Today and reminders. Nothing in them was deleted, and their dates stay on the calendar.</p>
    ${list.length ? '' : `<p class="mt-4 rounded-xl border border-dashed border-slate-200 py-6 text-center text-sm text-slate-400">Nothing archived yet. Archive a finished or paused project with ${ico('archive')} on its card; your agent can too.</p>`}
    <ul class="mt-3 space-y-2 max-h-[60vh] overflow-y-auto scroll-thin">${list.map(p => `
      <li class="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 p-2.5" data-arch="${p.id}">
        <a href="#/b/${p.id}" class="flex-1 min-w-0"><span class="block text-sm font-semibold text-slate-700 truncate">${esc(p.title)}</span>
          <span class="block text-xs text-slate-500">${pct(p.progress)}% · ${p.leaves_done}/${p.leaves_total} tasks · archived ${esc(fmtDay(p.archived_at.slice(0, 10)))}</span></a>
        <button data-unarch class="rounded-full border border-slate-300 px-3 py-1 text-xs font-semibold hover:bg-slate-50">Unarchive</button>
      </li>`).join('')}</ul>`, root => {
    root.querySelectorAll('a').forEach(a => a.addEventListener('click', () => closeModal()));
    root.querySelectorAll('[data-unarch]').forEach(btn => btn.onclick = async () => {
      const li = btn.closest('[data-arch]'), p = list.find(x => x.id === Number(li.dataset.arch));
      if (await setArchived(p, false)) { li.remove(); if (!root.querySelector('[data-arch]')) closeModal(); }
    });
  });
}

// Import: a Markdown outline (as a new project, or inside parentId) or a Nestwork JSON export.
function importModal({ parentId = null, parentTitle = '' } = {}) {
  openModal(`
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-lg font-semibold">${parentId ? `Import into “${esc(parentTitle)}”` : 'Import'}</h2>
      <button data-close class="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="Close">✕</button>
    </div>
    <p class="text-sm text-slate-600">Paste a Markdown outline, e.g. an Obsidian note. Headings and list items nest by level and indentation, <code class="text-xs">- [x]</code> items come in done, and plain lines under an item become its description.</p>
    <textarea id="im-text" rows="10" spellcheck="false" class="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-sm" placeholder="# Thesis&#10;## Research&#10;- [x] Read papers&#10;- [ ] Interviews&#10;  - Draft questions&#10;## Writing&#10;- Intro"></textarea>
    ${parentId ? '' : `<label class="block mt-2"><span class="block text-xs font-semibold text-slate-600 mb-1">Project name <span class="font-normal text-slate-400">(optional: a lone top heading is used otherwise)</span></span>
      <input id="im-title" maxlength="200" autocomplete="off" class="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"></label>`}
    <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
      ${parentId ? '<span></span>' : `<label class="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm cursor-pointer hover:bg-slate-50" title="A file exported from Nestwork (Export JSON)">${ico('upload')}Or a Nestwork export (.json)…<input id="im-file" type="file" accept=".json,application/json" class="hidden"></label>`}
      <div class="flex gap-2">
        <button data-close class="rounded-lg px-4 py-2 text-slate-600 hover:bg-slate-100">Cancel</button>
        <button id="im-go" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-4 py-2">Import</button>
      </div>
    </div>`, root => {
    const $ = s => root.querySelector(s);
    $('#im-text').focus();
    $('#im-go').onclick = async () => {
      const text = $('#im-text').value;
      if (!text.trim()) { $('#im-text').focus(); return; }
      const r = await attempt(() => api('POST', '/api/import/outline', { text, parent_id: parentId, title: $('#im-title')?.value.trim() || null }));
      if (!r) return;
      closeModal();
      if (parentId) refresh(); else location.hash = `#/b/${r.block_id}`;
    };
    $('#im-file')?.addEventListener('change', async e => {
      const file = e.target.files[0];
      if (!file) return;
      let data;
      try { data = JSON.parse(await file.text()); } catch { toast('That file isn’t JSON.', 'error'); return; }
      const r = await attempt(() => api('POST', '/api/import', { data }));
      if (r) { closeModal(); location.hash = r.projects.length === 1 ? `#/b/${r.projects[0]}` : '#/projects'; }
    });
  });
}
