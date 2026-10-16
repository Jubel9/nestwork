// Right-click menus made for Nestwork. What's under the pointer decides the menu: a to-do or project
// (open, add inside, edit, move to a phase, mark done, focus on it, duplicate, delete), a picture (open,
// save, use as the agent's profile picture), the agent (profile picture, gallery, chat), a chat message
// (copy), selected text (copy, search), or anywhere else (the app's main actions). Text fields and
// Shift + right-click keep the browser's own menu. Uses app.js, hud.js, chat.js, gallery.js, spotlight.js.

const cm = { menus: [], opener: null };
const SEP = { sep: true };
const cmSleep = ms => new Promise(r => setTimeout(r, ms));

// ---------------------------------------------------------------- what's under the pointer
function cmBlockId(t) {
  const el = t.closest('.card[data-id], [data-band], [data-go], [data-tnode], [data-task], [data-popup], a[href^="#/b/"]');
  if (!el) return null;
  const id = el.dataset.id || el.dataset.band || el.dataset.go || el.dataset.tnode || el.dataset.task || el.dataset.popup
    || (el.getAttribute('href') || '').match(/^#\/b\/(\d+)/)?.[1];
  return id ? Number(id) : null;
}
function cmPictureURL(t) {
  const img = t.closest('img');
  if (!img || img.closest('[data-agent-avatar]')) return null;
  if (!img.closest('.lightbox, [data-lightbox], .gal-tile, .pics')) return null;
  return new URL(img.src, location.href).pathname + new URL(img.src, location.href).search;
}

async function cmItemsFor(e) {
  const t = e.target, sel = String(window.getSelection() || '').trim();
  const lead = sel ? [
    { label: `Copy “${sel.length > 28 ? sel.slice(0, 28) + '…' : sel}”`, icon: 'note', run: () => cmCopy(sel, 'Copied.') },
    { label: 'Search for it', icon: 'search', kbd: SPOT_KEY, run: () => spotOpenWith(sel) },
    SEP] : [];
  const url = cmPictureURL(t);
  if (url) return [...lead, ...pictureMenu(url)];
  if (t.closest('[data-agent-avatar], [data-agent-gallery]')) return [...lead, ...agentMenu()];
  const id = cmBlockId(t);
  if (id) return [...lead, ...await blockMenu(id)];
  const msg = t.closest('#chat-log .msg');
  if (msg) return [...lead, ...messageMenu(msg)];
  return [...lead, ...globalMenu()];
}

// ---------------------------------------------------------------- menus
const currentProject = () => state.data ? (state.data.breadcrumb[0]?.id ?? state.data.block.id) : null;
// Run something that needs a project's board open (its phases, its to-do list) on that project's page.
async function onProject(projectId, fn) {
  if (currentProject() !== projectId) {
    location.hash = `#/b/${projectId}`;
    for (let i = 0; i < 40 && state.data?.block?.id !== projectId; i++) await cmSleep(75);
  }
  fn();
}

async function blockMenu(id) {
  let d;
  try { d = await api('GET', `/api/blocks/${id}`); } catch (e) { toast(e.message, 'error'); return globalMenu(); }
  registerStages(d.stages);
  const b = d.block, root = d.is_root, projectId = root ? id : d.breadcrumb[0].id, leaf = !b.children_total;
  const move = status => attempt(() => api('POST', `/api/blocks/${id}/move`, { status })).then(r => r && refresh());
  const last = d.stages[d.stages.length - 1];
  return [
    { head: b.title, sub: root ? `Project · ${pct(b.progress)}% done` : `${stageLabel(b.status)}${b.locked ? ' · waiting' : ''}` },
    root ? { label: 'Open project', icon: 'folder', run: () => { location.hash = `#/b/${id}`; } }
      : { label: 'Open', icon: 'search', run: () => spotOpenBlock(projectId, id) },
    ...(root ? [] : [{ label: 'Open as page', icon: 'undo', run: () => { location.hash = `#/b/${id}`; } }]),
    SEP,
    { label: 'New to-do inside…', icon: 'note', kbd: currentProject() === projectId ? 'N' : '', run: () => onProject(projectId, () => newTodoModal({ parentId: id, parentTitle: b.title })) },
    { label: root ? 'Edit project…' : 'Edit…', icon: 'cog', run: () => onProject(projectId, () => editDrawer(id, root)) },
    ...(root ? [] : [
      { label: 'Move to phase', icon: 'chart', sub: () => d.stages.map(s => ({
        label: s.label, dot: STAGE_STYLE[s.key].dot, checked: s.key === b.status, disabled: s.key === b.status, run: () => move(s.key) })) },
      ...(b.done ? [] : [{ label: `Mark done (${last.label})`, icon: 'unlock', run: () => move(last.key) }]),
      ...(leaf && !b.done ? [{ label: 'Focus on this', icon: 'timer', run: () => { setTimerTask(id, b.title, d.breadcrumb[0]?.title || ''); toast(`The focus timer is on “${b.title}”.`, 'success'); } }] : []),
    ]),
    ...(root ? [] : [{ label: 'Priority', icon: 'flag', sub: () => PRIORITY.map(([k, l]) => ({
      label: l, checked: Number(k) === (b.priority || 0), disabled: Number(k) === (b.priority || 0),
      run: () => attempt(() => api('PATCH', `/api/blocks/${id}`, { priority: Number(k) })).then(r => r && refresh()) })) }]),
    ...(root ? [{ label: b.archived_at ? 'Unarchive project' : 'Archive project', icon: 'archive', run: () => setArchived(b, !b.archived_at) },
      { label: 'Export (JSON)', icon: 'download', run: () => { location.href = `/api/blocks/${id}/export.json`; } }] : []),
    { label: 'Copy name', icon: 'note', run: () => cmCopy(b.title, 'Name copied.') },
    ...(root ? [] : [{ label: 'Duplicate', icon: 'archive', run: async () => { const r = await attempt(() => api('POST', `/api/blocks/${id}/duplicate`)); if (r) { toast(`Created “${r.block.title}”.`, 'success'); refresh(); } } }]),
    SEP,
    { label: root ? 'Delete project…' : 'Delete…', icon: 'warn', danger: true, run: async () => {
      const inside = b.leaves_total && b.children_total ? ` and the ${b.leaves_total} item(s) nested inside it` : '';
      if (!confirm(`Delete “${b.title}”${inside}? It goes to the trash (Settings → 🗑 Trash & backups) for 30 days.`)) return;
      const r = await attempt(() => api('DELETE', `/api/blocks/${id}`));
      if (!r) return;
      deletedToast(b.title, r.trash_id);
      if (location.hash === `#/b/${id}`) location.hash = root ? '#/projects' : `#/b/${d.breadcrumb[d.breadcrumb.length - 1].id}`;
      else refresh();
    } },
  ];
}

function pictureMenu(url) {
  const it = gal.items.find(i => i.url === url);
  const name = it?.name || url.split('/').pop().split('?')[0];
  return [
    { head: it ? (it.kind === 'selfie' ? 'Selfie' : 'Drawing') : 'Picture', sub: it ? fmtWhen(it.created_at) : '' },
    ...(document.querySelector('.lightbox') ? [] : [{ label: 'Open', icon: 'image', run: () => openLightbox(url, { list: it ? gal.items : null }) }]),
    { label: 'Save picture', icon: 'inbox', run: () => cmSave(url, name) },
    ...(it && !it.avatar ? [{ label: 'Use as profile picture', icon: 'user', run: () => setAvatar(it.name) }] : []),
    { label: 'Copy picture link', icon: 'note', run: () => cmCopy(new URL(url, location.href).href, 'Link copied.') },
    SEP,
    { label: 'Open the gallery', icon: 'image', run: () => openGallery() },
  ];
}

function agentMenu() {
  const name = chatName();
  return [
    { head: name, sub: 'Your agent' },
    { label: 'Profile picture', icon: 'user', run: () => openProfilePicture() },
    { label: 'Gallery', icon: 'image', run: () => openGallery() },
    ...(gal.avatar ? [{ label: 'Use the face photo again', icon: 'undo', run: () => setAvatar(null) }] : []),
    SEP,
    { label: document.body.classList.contains('chat-open') ? 'Hide the chat' : 'Open the chat', icon: 'chat', kbd: 'C', run: () => setChatOpen(!document.body.classList.contains('chat-open')) },
    { label: 'Agent settings', icon: 'cog', run: () => settingsModal('agent') },
  ];
}

function messageMenu(msg) {
  const body = msg.querySelector('.body') || msg;
  const text = body.innerText.trim();
  const pics = [...msg.querySelectorAll('[data-lightbox]')].map(p => p.dataset.lightbox);
  return [
    { head: msg.querySelector('.chat-md') ? `${chatName()} said` : 'You said', sub: '' },
    ...(text ? [{ label: 'Copy message', icon: 'note', run: () => cmCopy(text, 'Message copied.') }] : []),
    ...(pics.length ? [{ label: pics.length > 1 ? `Save ${pics.length} pictures` : 'Save picture', icon: 'inbox', run: () => pics.forEach((u, i) => setTimeout(() => cmSave(u, u.split('/').pop()), i * 250)) }] : []),
    SEP,
    { label: 'Gallery', icon: 'image', run: () => openGallery() },
  ];
}

function globalMenu() {
  const onProject = /^#\/b\/\d+/.test(location.hash) && state.data;
  const chatOpen = document.body.classList.contains('chat-open');
  return [
    ...(onProject ? [{ label: `New to-do in “${state.data.block.title}”…`, icon: 'note', kbd: 'N', run: () => newTodoModal() }] : []),
    { label: 'New project…', icon: 'folder', run: () => newProjectModal() },
    { label: 'Quick search', icon: 'search', kbd: SPOT_KEY, run: () => spotOpen() },
    ...(conn.v?.undo ? [{ label: `Undo: ${conn.v.undo.label}`, icon: 'undo', kbd: UNDO_KEY, run: undoLast }] : []),
    SEP,
    { label: hud.timer.endsAt ? 'Pause the focus timer' : 'Start the focus timer', icon: 'timer', kbd: 'F', run: () => { if (!onHudPage()) location.hash = '#/'; timerToggle(); } },
    { label: chatOpen ? 'Hide the chat' : 'Open the chat', icon: 'chat', kbd: 'C', run: () => setChatOpen(!chatOpen) },
    { label: 'Go to', icon: 'chart', sub: () => [['HUD', '#/'], ['Focus', '#/focus'], ['Workflow', '#/projects'], ['Calendar', '#/calendar'], ['Agent’s Work', '#/work']].map(([l, h]) => ({
      label: l, checked: (h === '#/' ? ['', '#/'].includes(location.hash) : location.hash.startsWith(h)), run: () => { location.hash = h; } })) },
    { label: 'Theme', icon: 'palette', sub: () => THEME_CHOICES.map(([k, l]) => ({ label: l, checked: theme === k, run: () => setTheme(k) })) },
    SEP,
    { label: 'Settings', icon: 'cog', run: () => settingsModal() },
    { label: 'How it works', icon: 'help', run: () => settingsModal('help') },
  ];
}

// ---------------------------------------------------------------- helpers
function cmCopy(text, done) {
  navigator.clipboard?.writeText(text).then(() => toast(done, 'success'), () => toast('Couldn’t copy: your browser said no.', 'error'));
}
function cmSave(url, name) {
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
}
function spotOpenWith(text) {
  spotOpen();
  const q = document.getElementById('spot-q');
  q.value = text.slice(0, 80);
  q.dispatchEvent(new Event('input'));
}

// ---------------------------------------------------------------- the menu itself
function cmRender(items, level) {
  const el = document.createElement('div');
  el.className = 'cm fixed z-[90] min-w-[14rem] max-w-[20rem] max-h-[calc(100dvh-12px)] overflow-y-auto overscroll-contain scroll-thin py-1 bg-white border border-lavender-200 shadow-xl';
  el.setAttribute('role', 'menu');
  el.tabIndex = -1;
  el.innerHTML = items.map((it, i) => it.sep ? '<div class="my-1 h-px bg-lavender-100" role="separator"></div>'
    : it.head !== undefined ? `<div class="px-3 pt-1.5 pb-1 border-b border-lavender-100 mb-1"><p class="truncate text-sm font-bold text-slate-800">${esc(it.head)}</p>${it.sub ? `<p class="truncate text-[11px] text-slate-400">${esc(it.sub)}</p>` : ''}</div>`
    : `<button type="button" role="menuitem" data-cm="${i}" ${it.disabled ? 'aria-disabled="true"' : ''} ${it.sub ? 'aria-haspopup="menu"' : ''}
        class="cm-item flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm ${it.danger ? 'text-blush-700' : 'text-slate-700'} ${it.disabled ? 'opacity-60' : ''}">
        <span class="w-4 shrink-0 flex justify-center ${it.danger ? '' : 'text-lavender-700'}">${it.dot ? `<span class="w-2 h-2 ${it.dot}"></span>` : it.icon ? ico(it.icon) : ''}</span>
        <span class="flex-1 truncate">${esc(it.label)}</span>
        ${it.checked ? '<span class="text-lavender-700">✓</span>' : ''}
        ${it.kbd ? `<kbd class="hud-kbd">${esc(it.kbd)}</kbd>` : ''}${it.sub ? '<span class="text-slate-400">›</span>' : ''}
      </button>`).join('')
    + (level === 0 ? `<p class="mt-1 px-3 pt-1 border-t border-lavender-100 text-[10px] text-slate-400">Shift + right-click: browser menu</p>` : '');
  el._items = items;
  el._level = level;
  return el;
}

function cmPlace(el, x, y, alongside = null) {
  document.body.appendChild(el);
  const w = el.offsetWidth, h = el.offsetHeight, vw = innerWidth, vh = innerHeight;
  if (alongside) {   // a submenu: to the right of its item, or the left when there's no room
    const r = alongside.getBoundingClientRect();
    x = r.right + w + 4 <= vw ? r.right + 2 : r.left - w - 2;
    y = r.top - 5;
  }
  x = Math.max(6, Math.min(x, vw - w - 6));
  y = Math.max(6, Math.min(y, vh - h - 6));
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  el.style.transformOrigin = alongside ? 'left top' : 'left top';
  animate(el, 'cm-in');
  stagger(el.querySelectorAll('.cm-item'), 'cm-item-in', 18, 160);
}

function cmCloseFrom(level) {
  while (cm.menus.length > level) {
    const m = cm.menus.pop();
    animateOut([m], 'fade-out', 110).then(() => m.remove());
  }
}
function cmClose() {
  if (!cm.menus.length) return;
  cmCloseFrom(0);
  document.removeEventListener('keydown', cmKey, true);
}

function cmOpenSub(menu, btn) {
  const it = menu._items[Number(btn.dataset.cm)];
  cmCloseFrom(menu._level + 1);
  const sub = cmRender(it.sub(), menu._level + 1);
  cm.menus.push(sub);
  cmPlace(sub, 0, 0, btn);
  btn.classList.add('is-open');
  return sub;
}

function cmActivate(menu, btn) {
  const it = menu._items[Number(btn.dataset.cm)];
  if (!it || it.disabled) return;
  if (it.sub) { cmOpenSub(menu, btn).querySelector('.cm-item:not([aria-disabled])')?.focus(); return; }
  cmClose();
  Promise.resolve().then(it.run).catch(e => toast(e.message, 'error'));
}

function cmKey(e) {
  if (!cm.menus.length) return;
  const menu = cm.menus.find(m => m.contains(document.activeElement)) || cm.menus[cm.menus.length - 1];
  const buttons = [...menu.querySelectorAll('.cm-item:not([aria-disabled])')];
  const at = buttons.indexOf(document.activeElement);
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cmClose(); }
  else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const n = buttons.length;
    buttons[((at < 0 ? (e.key === 'ArrowDown' ? -1 : 0) : at) + (e.key === 'ArrowDown' ? 1 : -1) + n) % n]?.focus();
  }
  else if (e.key === 'ArrowRight' && at >= 0 && menu._items[Number(buttons[at].dataset.cm)].sub) { e.preventDefault(); cmOpenSub(menu, buttons[at]).querySelector('.cm-item:not([aria-disabled])')?.focus(); }
  else if (e.key === 'ArrowLeft' && menu._level > 0) { e.preventDefault(); cmCloseFrom(menu._level); cm.menus[menu._level - 1].querySelector('.is-open')?.focus(); }
  else if ((e.key === 'Enter' || e.key === ' ') && at >= 0) { e.preventDefault(); cmActivate(menu, buttons[at]); }
}

document.addEventListener('contextmenu', async e => {
  if (e.shiftKey || e.target.closest('input, textarea, select, [contenteditable="true"], #spot')) return;
  if (location.hash.startsWith('#/awaken')) return;
  e.preventDefault();
  cmClose();
  const x = e.clientX, y = e.clientY;
  const items = await cmItemsFor(e);
  const menu = cmRender(items, 0);
  cm.menus = [menu];
  cmPlace(menu, x, y);
  menu.focus({ preventScroll: true });
  document.addEventListener('keydown', cmKey, true);
});

document.addEventListener('click', e => {
  const menu = e.target.closest('.cm');
  if (!menu) return;
  const btn = e.target.closest('.cm-item');
  if (btn) cmActivate(menu, btn);
});
document.addEventListener('mouseover', e => {
  const menu = e.target.closest('.cm'), btn = e.target.closest('.cm-item');
  if (!menu || !btn) return;
  btn.focus({ preventScroll: true });
  const it = menu._items[Number(btn.dataset.cm)];
  if (it?.sub && !btn.classList.contains('is-open')) cmOpenSub(menu, btn);
  else if (!it?.sub) { cmCloseFrom(menu._level + 1); menu.querySelectorAll('.is-open').forEach(b => b.classList.remove('is-open')); }
});
document.addEventListener('mousedown', e => { if (cm.menus.length && !e.target.closest('.cm')) cmClose(); }, true);
['resize', 'blur'].forEach(ev => window.addEventListener(ev, cmClose));
document.addEventListener('scroll', e => { if (!e.target.closest?.('.cm')) cmClose(); }, true);
window.addEventListener('hashchange', cmClose);
