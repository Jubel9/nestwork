// Notes & activity on a block: a log under its description. You write notes here; your agent writes them
// when you ask (MCP add_note); moves and focus sessions are added by Nestwork. Shown in the block popup,
// as the 📝 Notes tab of a block page, and under a single task's page. Uses app.js and chat.js helpers.

const notesCache = new Map();   // block id -> last timeline, so redraws don't jump while it reloads

const NOTE_KIND = {
  you: { icon: '🙋', ring: 'bg-lavender-100' },
  agent: { icon: '✦', ring: 'bg-gradient-to-br from-lilac-300 via-baby-300 to-lavender-300 text-white' },
  event: { icon: '⇄', ring: 'bg-slate-100 text-slate-500' },
  focus: { icon: '⏱', ring: 'bg-mint-100' },
};

function noteWhen(stamp) {
  const d = new Date(stamp), now = new Date();
  const mins = Math.round((now - d) / 60e3);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const sameDay = d.toDateString() === now.toDateString();
  const yesterday = new Date(now - 864e5).toDateString() === d.toDateString();
  const time = d.toTimeString().slice(0, 5);
  if (sameDay) return `today ${time}`;
  if (yesterday) return `yesterday ${time}`;
  return `${d.toLocaleDateString([], { day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' })} ${time}`;
}

function noteItemHTML(n) {
  const k = n.kind === 'note' ? (n.author === 'agent' ? 'agent' : 'you') : n.kind;
  const who = n.kind === 'focus' ? 'Focus timer' : n.author === 'agent' ? (n.author_name || chat.agentName || 'Your agent') : 'You';
  const body = n.kind === 'note' ? `<div class="chat-md text-sm text-slate-700 break-words">${md(n.text)}</div>`
    : `<p class="text-xs text-slate-500 whitespace-pre-line">${esc(n.text)}</p>`;
  return `<li class="group flex gap-2.5" data-note="${esc(String(n.id))}">
    <span class="mt-0.5 w-6 h-6 shrink-0 rounded-full ${NOTE_KIND[k].ring} flex items-center justify-center text-[11px]">${NOTE_KIND[k].icon}</span>
    <div class="min-w-0 flex-1 ${n.kind === 'note' ? 'rounded-2xl rounded-tl-md bg-white border border-lavender-100 px-3 py-2' : 'py-0.5'}">
      <div class="flex items-center gap-2 text-[11px] text-slate-400">
        <span class="font-semibold ${n.kind === 'note' ? 'text-slate-600' : ''}">${esc(who)}</span>
        <span title="${esc(new Date(n.created_at).toLocaleString())}">${noteWhen(n.created_at)}</span>${n.edited_at ? '<span>· edited</span>' : ''}
        ${n.kind === 'note' ? `<span class="ml-auto flex gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
          <button data-note-edit class="rounded px-1.5 hover:bg-slate-100 hover:text-slate-700">Edit</button>
          <button data-note-del class="rounded px-1.5 hover:bg-blush-50 hover:text-blush-700">Delete</button></span>` : ''}
      </div>
      <div class="note-body mt-0.5">${body}</div>
    </div>
  </li>`;
}

function notesListHTML(items, filter) {
  const shown = filter === 'notes' ? items.filter(i => i.kind === 'note') : items;
  if (!shown.length) return `<p class="text-sm text-slate-400 py-2">${filter === 'notes' && items.length ? 'No notes yet, only activity.' : 'Nothing logged yet. Write what you did, decided or found, or ask your agent to.'}</p>`;
  return `<ul class="space-y-2.5">${shown.map(noteItemHTML).join('')}</ul>`;
}

// The section itself. `fill` makes the list take the leftover height and scroll (block page tab).
function notesPanelHTML(b, { fill = false } = {}) {
  const items = notesCache.get(b.id);
  const filter = localGet('nestwork.notesFilter') || 'all';
  return `<section class="notes ${fill ? 'flex flex-col min-h-0 h-full' : ''}" data-notes="${b.id}">
    <div class="shrink-0 flex flex-wrap items-center justify-between gap-2">
      <h3 class="text-sm font-bold text-slate-700">📝 Notes &amp; activity</h3>
      <div class="flex rounded-full border border-slate-200 p-0.5 text-[11px] font-semibold" role="group" aria-label="Show">
        ${[['all', 'Everything'], ['notes', 'Notes only']].map(([k, l]) => `<button data-notes-filter="${k}" aria-pressed="${filter === k}" class="rounded-full px-2.5 py-0.5 ${filter === k ? 'bg-lavender-100 text-lavender-800' : 'text-slate-500 hover:text-slate-800'}">${l}</button>`).join('')}
      </div>
    </div>
    <form data-notes-form class="shrink-0 mt-2 rounded-2xl border border-lavender-200 bg-white focus-within:ring-2 focus-within:ring-lavender-300">
      <textarea data-notes-input rows="2" maxlength="10000" placeholder="Write a note: progress, a decision, a link… (Markdown works)" class="block w-full resize-none rounded-2xl bg-transparent px-3 pt-2 text-sm focus:outline-none"></textarea>
      <div class="flex items-center justify-between gap-2 px-2 pb-2">
        <button type="button" data-notes-agent class="rounded-full px-2.5 py-1 text-[11px] font-semibold text-lavender-700 hover:bg-lavender-50" title="Opens the chat with a request you can finish">✦ Ask ${esc(chat.agentName || 'my agent')} to write one</button>
        <span class="flex items-center gap-2"><span class="hidden sm:inline text-[10px] text-slate-400">⌘/Ctrl + Enter</span>
        <button class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-bold px-3 py-1">Add note</button></span>
      </div>
    </form>
    <div data-notes-list class="mt-3 ${fill ? 'flex-1 min-h-0 overflow-y-auto scroll-thin pr-1' : ''}">${items ? notesListHTML(items, filter) : '<p class="text-sm text-slate-400">Loading…</p>'}</div>
  </section>`;
}

async function loadNotes(section, id) {
  try {
    const items = await api('GET', `/api/blocks/${id}/notes`);
    notesCache.set(id, items);
    if (section.isConnected) section.querySelector('[data-notes-list]').innerHTML = notesListHTML(items, localGet('nestwork.notesFilter') || 'all');
  } catch { /* the block was deleted: the page handles that */ }
}

function bindNotes(root, b) {
  const section = root.querySelector(`[data-notes="${b.id}"]`);
  if (!section) return;
  const input = section.querySelector('[data-notes-input]');
  const list = section.querySelector('[data-notes-list]');
  const redraw = () => { list.innerHTML = notesListHTML(notesCache.get(b.id) || [], localGet('nestwork.notesFilter') || 'all'); };
  const grow = () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 200)}px`; };
  input.addEventListener('input', grow);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); section.querySelector('[data-notes-form]').requestSubmit(); }
    if (e.key === 'Escape') e.stopPropagation();
  });
  section.querySelector('[data-notes-form]').onsubmit = async e => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return input.focus();
    const n = await attempt(() => api('POST', `/api/blocks/${b.id}/notes`, { text }));
    if (!n) return;
    input.value = '';
    grow();
    notesCache.set(b.id, [n, ...(notesCache.get(b.id) || [])]);
    redraw();
    animate(list.querySelector('li'), 'pop-in');
    poll(true);
  };
  section.querySelector('[data-notes-agent]').onclick = () => {
    setChatOpen(true);
    chat.input.value = `Add a note to “${b.title}” (id ${b.id}): `;
    chat.input.dispatchEvent(new Event('input'));
    chat.input.focus();
    chat.input.setSelectionRange(chat.input.value.length, chat.input.value.length);
  };
  section.querySelectorAll('[data-notes-filter]').forEach(btn => btn.onclick = () => {
    localSet('nestwork.notesFilter', btn.dataset.notesFilter);
    section.querySelectorAll('[data-notes-filter]').forEach(x => {
      const on = x === btn;
      x.setAttribute('aria-pressed', on);
      x.className = `rounded-full px-2.5 py-0.5 ${on ? 'bg-lavender-100 text-lavender-800' : 'text-slate-500 hover:text-slate-800'}`;
    });
    redraw();
  });
  list.addEventListener('click', async e => {
    const li = e.target.closest('[data-note]');
    if (!li) return;
    const nid = Number(li.dataset.note), note = (notesCache.get(b.id) || []).find(n => n.id === nid);
    if (e.target.closest('[data-note-del]')) {
      if (!confirm('Delete this note?')) return;
      if (await attempt(() => api('DELETE', `/api/notes/${nid}`))) {
        notesCache.set(b.id, notesCache.get(b.id).filter(n => n.id !== nid));
        await animateOut([li], 'poof', 200);
        redraw();
        poll(true);
      }
    } else if (e.target.closest('[data-note-edit]') && note) {
      const box = li.querySelector('.note-body');
      box.innerHTML = `<textarea rows="3" class="w-full rounded-lg border border-lavender-200 bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-lavender-300">${esc(note.text)}</textarea>
        <div class="mt-1 flex justify-end gap-1.5"><button data-note-cancel class="rounded-full px-3 py-1 text-xs text-slate-500 hover:bg-slate-100">Cancel</button>
        <button data-note-save class="rounded-full bg-lavender-700 text-white px-3 py-1 text-xs font-bold hover:bg-lavender-800">Save</button></div>`;
      const ta = box.querySelector('textarea');
      ta.focus();
      ta.onkeydown = ev => {
        if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) box.querySelector('[data-note-save]').click();
        if (ev.key === 'Escape') { ev.stopPropagation(); redraw(); }
      };
      box.querySelector('[data-note-cancel]').onclick = redraw;
      box.querySelector('[data-note-save]').onclick = async () => {
        const saved = await attempt(() => api('PATCH', `/api/notes/${nid}`, { text: ta.value }));
        if (!saved) return;
        notesCache.set(b.id, notesCache.get(b.id).map(n => n.id === nid ? saved : n));
        redraw();
      };
    }
  });
  loadNotes(section, b.id);
}

// The block page's 📝 Notes tab.
function renderNotesView(view, data) {
  view.innerHTML = `<div class="rounded-3xl border border-lavender-100 bg-white/70 p-4 shadow-sm lg:h-full">${notesPanelHTML(data.block, { fill: true })}</div>`;
  bindNotes(view, data.block);
}
