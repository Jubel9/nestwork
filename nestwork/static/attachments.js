// Links & files on a block: a strip under its description (page and popup) with each link or file, plus
// "+ Link" and "+ File" (or drop files on it). Files open in a new tab (images and PDFs show inline). Removing
// one can be undone with ⌘Z. Uses app.js (api, esc, toast, ico, attempt). Server side: nestwork/attachments.py.

const filesCache = new Map();   // block id -> last list, so redraws don't flicker
const FILE_MAX = 25 * 1024 * 1024;

function fileSize(n) { return n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`; }

function attachmentItemHTML(a) {
  const isImg = a.kind === 'file' && (a.mime || '').startsWith('image/');
  const sub = a.kind === 'link' ? (() => { try { return new URL(a.url).hostname.replace(/^www\./, '') || a.url; } catch { return a.url; } })()
    : `${fileSize(a.size || 0)}${a.added_by === 'agent' ? ' · by your agent' : ''}`;
  return `<li class="group flex items-center gap-2 rounded-lg border border-lavender-100 bg-white px-2 py-1.5 min-w-0" data-file="${a.id}">
    ${isImg ? `<img src="${esc(a.href)}" alt="" class="w-7 h-7 object-cover rounded shrink-0">` : `<span class="w-7 h-7 shrink-0 flex items-center justify-center rounded bg-lavender-50 text-lavender-700">${ico(a.kind === 'link' ? 'link' : 'paperclip')}</span>`}
    <a href="${esc(a.href)}" target="_blank" rel="noopener noreferrer" class="min-w-0 flex-1">
      <span class="block truncate text-sm font-semibold text-slate-700 hover:text-lavender-800">${esc(a.title)}</span>
      <span class="block truncate text-[11px] text-slate-400">${esc(sub)}</span></a>
    <button data-file-del class="shrink-0 p-1 rounded text-slate-400 opacity-0 group-hover:opacity-100 focus:opacity-100 hover:text-blush-700 hover:bg-blush-50" title="Remove (${UNDO_KEY} brings it back)" aria-label="Remove">✕</button>
  </li>`;
}

function attachmentsPanelHTML(b) {
  const list = filesCache.get(b.id);
  return `<section class="mt-3" data-files="${b.id}">
    <div class="flex flex-wrap items-center gap-2">
      <h3 class="text-[11px] font-bold uppercase tracking-wider text-slate-400 inline-flex items-center gap-1">${ico('paperclip')}Links &amp; files${list?.length ? ` <span class="hud-num">${list.length}</span>` : ''}</h3>
      <button data-file-addlink class="rounded-full border border-lavender-200 px-2.5 py-0.5 text-xs font-semibold text-lavender-700 hover:bg-lavender-50">+ Link</button>
      <label class="rounded-full border border-lavender-200 px-2.5 py-0.5 text-xs font-semibold text-lavender-700 hover:bg-lavender-50 cursor-pointer">+ File<input type="file" multiple data-file-input class="hidden"></label>
      <span class="text-[11px] text-slate-400">or drop files here</span>
    </div>
    <form data-file-linkform class="hidden mt-2 flex flex-wrap gap-2">
      <input name="url" required placeholder="https://… or obsidian://…" autocomplete="off" class="flex-1 min-w-[12rem] rounded-lg border border-slate-300 px-2.5 py-1 text-sm">
      <input name="title" placeholder="Name (optional)" autocomplete="off" class="w-44 rounded-lg border border-slate-300 px-2.5 py-1 text-sm">
      <button class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-bold px-3 py-1">Add link</button>
    </form>
    <ul data-files-list class="mt-2 grid gap-1.5 sm:grid-cols-2">${(list || []).map(attachmentItemHTML).join('')}</ul>
  </section>`;
}

async function loadAttachments(section, id) {
  try { filesCache.set(id, await api('GET', `/api/blocks/${id}/attachments`)); } catch { return; }
  const ul = section.querySelector('[data-files-list]');
  if (!ul) return;
  ul.innerHTML = filesCache.get(id).map(attachmentItemHTML).join('');
  const h = section.querySelector('h3');
  if (h) h.innerHTML = `${ico('paperclip')}Links &amp; files${filesCache.get(id).length ? ` <span class="hud-num">${filesCache.get(id).length}</span>` : ''}`;
}

function bindAttachments(root, b) {
  const section = root.querySelector(`[data-files="${b.id}"]`);
  if (!section) return;
  const reload = () => loadAttachments(section, b.id).then(() => renderCurrent());
  const form = section.querySelector('[data-file-linkform]');
  section.querySelector('[data-file-addlink]').onclick = () => { form.classList.toggle('hidden'); form.url.focus(); };
  form.onsubmit = async e => {
    e.preventDefault();
    if (await attempt(() => api('POST', `/api/blocks/${b.id}/attachments`, { url: form.url.value, title: form.title.value }))) {
      form.reset(); form.classList.add('hidden'); reload();
    }
  };
  const upload = async files => {
    for (const file of files) {
      if (file.size > FILE_MAX) { toast(`“${file.name}” is over 25 MB.`, 'error'); continue; }
      const data = await new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = fail; r.readAsDataURL(file); });
      await attempt(() => api('POST', `/api/blocks/${b.id}/attachments`, { name: file.name, data, mime: file.type }));
    }
    reload();
  };
  section.querySelector('[data-file-input]').onchange = e => upload([...e.target.files]);
  section.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); section.classList.add('ring-2', 'ring-lavender-300', 'rounded-xl'); } });
  section.addEventListener('dragleave', e => { if (!section.contains(e.relatedTarget)) section.classList.remove('ring-2', 'ring-lavender-300', 'rounded-xl'); });
  section.addEventListener('drop', e => {
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    section.classList.remove('ring-2', 'ring-lavender-300', 'rounded-xl');
    upload([...e.dataTransfer.files]);
  });
  section.addEventListener('click', async e => {
    const del = e.target.closest('[data-file-del]');
    if (!del) return;
    const li = del.closest('[data-file]');
    if (await attempt(() => api('DELETE', `/api/attachments/${li.dataset.file}`))) {
      toast(`Removed. ${UNDO_KEY} brings it back.`, 'success');
      reload();
    }
  });
  loadAttachments(section, b.id);
}
