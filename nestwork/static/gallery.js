// The agent's gallery (every drawing and selfie it generated, plus a tab with the files you sent it in the
// chat, by category), its profile picture, and the lightbox.
// Click the agent's name in the chat header for the gallery; click its picture (chat or HUD) to see the
// profile picture. In the lightbox: ← → to browse, Save, and "Use as profile picture".
// Uses app.js (api, esc, toast, animate, openModal, ico) and chat.js (chat, md).

// gal, avatarURL and agentAvatarHTML live in app.js: the chat and HUD draw avatars before this file has loaded.

async function loadGallery() {
  const g = await api('GET', '/api/agent/gallery');
  gal.items = g.items;
  gal.avatar = g.avatar;
  return g.items;
}

async function setAvatar(name) {
  const r = await attempt(() => api('PUT', '/api/agent/avatar', { name }));
  if (!r) return false;
  gal.avatar = r.avatar;
  gal.items.forEach(i => { i.avatar = i.name === r.avatar; });
  if (typeof chat !== 'undefined') chat.avatar = r.avatar;
  refreshAvatars();
  toast(r.avatar ? `New profile picture for ${chatName()}.` : `${chatName()} uses the face photo again.`, 'success');
  return true;
}
const chatName = () => (typeof chat !== 'undefined' && chat.agentName) || 'your agent';
const fmtWhen = iso => new Date(iso).toLocaleString([], { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });

// ---------------------------------------------------------------- the gallery
// Two tabs: the agent's pictures (selfies, drawings) and the files you sent it in the chat, by category.
const fileExt = name => (name.match(/\.([a-z0-9]{1,5})$/i)?.[1] || 'file').toUpperCase();
const shortWhen = iso => new Date(iso).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

function pictureTileHTML(i) {
  return `<figure class="gal-tile group relative aspect-square overflow-hidden rounded-md border ${i.avatar ? 'border-lilac-300' : 'border-lavender-200'} bg-slate-100" data-gal-name="${esc(i.name)}">
    <button data-gal-open="${esc(i.url)}" class="block w-full h-full" aria-label="Open this ${i.kind === 'selfie' ? 'selfie' : 'drawing'}">
      <img src="${esc(i.url)}" alt="${i.kind === 'selfie' ? 'Selfie' : 'Drawing'} from ${esc(fmtWhen(i.created_at))}" loading="lazy" class="w-full h-full object-cover transition duration-300 group-hover:scale-105">
    </button>
    <figcaption class="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/75 to-transparent px-2 pt-6 pb-1.5 text-[11px] text-white/90 flex justify-between gap-2" style="color: rgb(255 255 255 / .92)">
      <span>${i.kind === 'selfie' ? 'Selfie' : 'Drawing'}</span><span class="hud-num truncate">${esc(shortWhen(i.created_at))}</span>
    </figcaption>
    ${i.avatar ? '<span class="absolute top-1.5 left-1.5 rounded px-1.5 py-0.5 text-[10px] font-bold bg-lilac-300 text-white">Profile picture</span>'
      : `<button data-gal-avatar="${esc(i.name)}" class="absolute top-1.5 left-1.5 rounded px-1.5 py-0.5 text-[10px] font-bold bg-black/60 text-white opacity-0 group-hover:opacity-100 focus:opacity-100 transition hover:bg-lavender-700">Use as profile picture</button>`}
  </figure>`;
}

// A file you sent: pictures as thumbnails (open in the lightbox), anything else as a card that opens or downloads it.
function fileTileHTML(f) {
  const sub = `${fileSize(f.size || 0)} · ${esc(shortWhen(f.created_at))}`;
  if (f.kind === 'image' && f.mime !== 'image/svg+xml') {
    return `<figure class="gal-tile group relative aspect-square overflow-hidden rounded-md border border-lavender-200 bg-slate-100" title="${esc(f.message?.text || f.name)}">
      <button data-gal-open="${esc(f.url)}" class="block w-full h-full" aria-label="Open ${esc(f.name)}">
        <img src="${esc(f.url)}" alt="${esc(f.name)}" loading="lazy" class="w-full h-full object-cover transition duration-300 group-hover:scale-105"></button>
      <figcaption class="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/75 to-transparent px-2 pt-6 pb-1.5 text-[11px]" style="color: rgb(255 255 255 / .92)">
        <span class="block truncate font-semibold">${esc(f.name)}</span><span class="hud-num block truncate opacity-75">${sub}</span></figcaption>
    </figure>`;
  }
  const tint = { pdf: 'text-blush-700 bg-blush-50', document: 'text-baby-700 bg-baby-50', image: 'text-mint-700 bg-mint-50' }[f.kind] || 'text-slate-600 bg-slate-100';
  return `<a href="${esc(f.url)}" target="_blank" rel="noopener" class="gal-tile group flex flex-col aspect-square overflow-hidden rounded-md border border-lavender-200 bg-white hover:border-lavender-300 transition" title="${esc(f.message?.text || f.name)}">
    <span class="flex-1 min-h-0 flex flex-col items-center justify-center gap-1.5 ${tint} transition group-hover:brightness-95">
      <span class="text-2xl">${ico('paperclip')}</span><span class="hud-num text-sm font-bold tracking-wider">${esc(fileExt(f.name))}</span></span>
    <span class="shrink-0 px-2 py-1.5 text-[11px]">
      <span class="block truncate font-semibold text-slate-700">${esc(f.name)}</span><span class="hud-num block truncate text-slate-400">${sub}</span></span>
  </a>`;
}

async function openGallery(tab = 'pictures') {
  let files = [], kinds = [];
  try {
    const [, f] = await Promise.all([loadGallery(), api('GET', '/api/chat/files')]);
    files = f.items; kinds = f.kinds;
  } catch (e) { return toast(e.message, 'error'); }
  const filters = { pictures: 'all', files: 'all' };
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  openModal(`
    <div class="shrink-0 flex flex-wrap items-center justify-between gap-3 mb-3">
      <div class="flex items-center gap-3 min-w-0">
        ${agentAvatarHTML('w-10 h-10', 'text-base')}
        <div class="min-w-0"><h2 class="text-lg font-semibold truncate">${esc(chatName())}’s gallery</h2>
          <p data-gal-count class="text-xs text-slate-400"></p></div>
      </div>
      <button data-close class="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="Close">✕</button>
    </div>
    <div class="shrink-0 flex flex-wrap items-end justify-between gap-2 mb-4 border-b border-lavender-100">
      <div class="flex gap-1 text-sm" role="tablist" aria-label="Gallery">
        ${[['pictures', 'Pictures'], ['files', 'Your files']].map(([k, l]) => `<button data-gal-tab="${k}" role="tab" class="-mb-px border-b-2 px-3 py-1.5 font-semibold">${l} <span data-gal-tabn="${k}" class="hud-num text-xs opacity-70"></span></button>`).join('')}
      </div>
      <div data-gal-filters class="inline-flex flex-wrap rounded-full border border-lavender-200 p-0.5 mb-1.5 text-xs" aria-label="Show"></div>
    </div>
    <div class="flex-1 min-h-0 overflow-y-auto overscroll-contain scroll-thin -mx-1 px-1 pb-1">
      <div data-gal-grid class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3"></div>
    </div>`, root => {
    root.style.zIndex = '50';   // above the chat panel it's usually opened from
    // Never taller than the window: the title, tabs and filters stay put, the tiles scroll inside.
    Object.assign(root.firstElementChild.style, { display: 'flex', flexDirection: 'column', overflow: 'hidden' });
    const grid = root.querySelector('[data-gal-grid]');
    const options = () => tab === 'pictures'
      ? [['all', 'All', gal.items.length], ['selfie', 'Selfies', gal.items.filter(i => i.kind === 'selfie').length], ['image', 'Drawings', gal.items.filter(i => i.kind === 'image').length]]
      : [['all', 'All', files.length], ...kinds.map(k => [k.key, k.label, files.filter(f => f.kind === k.key).length]).filter(([, , n]) => n)];
    const shown = () => (tab === 'pictures' ? gal.items : files).filter(i => filters[tab] === 'all' || i.kind === filters[tab]);
    const draw = () => {
      if (!options().some(([k]) => k === filters[tab])) filters[tab] = 'all';
      root.querySelector('[data-gal-count]').textContent = tab === 'pictures'
        ? `${plural(gal.items.length, 'picture')} · ${gal.items.filter(i => i.kind === 'selfie').length} selfies · ${gal.items.filter(i => i.kind === 'image').length} drawings`
        : `${plural(files.length, 'file')} you sent in the chat`;
      root.querySelector('[data-gal-tabn="pictures"]').textContent = gal.items.length;
      root.querySelector('[data-gal-tabn="files"]').textContent = files.length;
      root.querySelectorAll('[data-gal-tab]').forEach(b => {
        const on = b.dataset.galTab === tab;
        b.className = `-mb-px border-b-2 px-3 py-1.5 font-semibold ${on ? 'border-lavender-700 text-lavender-800' : 'border-transparent text-slate-500 hover:text-lavender-700'}`;
        b.setAttribute('aria-selected', on);
      });
      root.querySelector('[data-gal-filters]').innerHTML = options().map(([k, l, n]) => {
        const on = k === filters[tab];
        return `<button data-gal-filter="${k}" aria-pressed="${on}" class="rounded-full px-3 py-1 font-semibold ${on ? 'bg-lavender-700 text-white' : 'text-slate-500 hover:text-lavender-700'}">${l}${tab === 'files' ? ` <span class="opacity-70">${n}</span>` : ''}</button>`;
      }).join('');
      const list = shown();
      grid.innerHTML = list.length ? list.map(tab === 'pictures' ? pictureTileHTML : fileTileHTML).join('')
        : `<p class="col-span-full py-12 text-center text-sm text-slate-400">${(tab === 'pictures' ? gal.items : files).length ? 'Nothing in this filter.'
          : tab === 'pictures' ? `No pictures yet. Ask ${esc(chatName())} for a selfie or a drawing in the chat.`
          : `No files yet. Attach pictures, PDFs or documents to a chat message with 📎, paste or drop.`}</p>`;
      stagger(grid.querySelectorAll('.gal-tile'), 'pop-in', 30, 400);
    };
    draw();
    root.addEventListener('click', async e => {
      const t = e.target.closest('[data-gal-tab]');
      if (t) { tab = t.dataset.galTab; draw(); return; }
      const f = e.target.closest('[data-gal-filter]');
      if (f) { filters[tab] = f.dataset.galFilter; draw(); return; }
      const set = e.target.closest('[data-gal-avatar]');
      if (set) { if (await setAvatar(set.dataset.galAvatar)) draw(); return; }
      const open = e.target.closest('[data-gal-open]');
      if (!open) return;
      if (tab === 'pictures') openLightbox(open.dataset.galOpen, { list: shown(), onChange: draw });
      else openLightbox(open.dataset.galOpen, { list: shown().filter(x => x.kind === 'image' && x.mime !== 'image/svg+xml').map(x => ({ ...x, upload: true })) });
    });
  }, { xl: true, stack: true });
}

// ---------------------------------------------------------------- lightbox
// One picture, full size, with browsing (← →), Save and "Use as profile picture" for gallery pictures.
function openLightbox(url, { list = null, onChange = null, title = null } = {}) {
  const items = list || gal.items;
  let i = Math.max(0, items.findIndex(x => x.url === url));
  const item = () => items.find(x => x.url === url) || null;
  const box = document.createElement('div');
  box.className = 'lightbox fixed inset-0 z-[60] bg-black/85 backdrop-blur-sm flex flex-col items-center justify-center gap-3 p-4';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-label', 'Picture');
  const draw = () => {
    const it = item(), many = list && items.length > 1;
    const name = it?.name || url.split('/').pop().split('?')[0];
    box.innerHTML = `
      <div class="lb-frame relative max-w-[92vw]">
        <img src="${esc(url)}" alt="${esc(title || (it ? (it.upload ? it.name : `${it.kind === 'selfie' ? 'Selfie' : 'Drawing'} from ${fmtWhen(it.created_at)}`) : 'Picture'))}" data-lb-img class="block max-w-[92vw] max-h-[calc(100dvh-8rem)] rounded-md border border-lavender-300/60 shadow-2xl">
        ${many ? `<button data-lb-prev class="lb-nav left-2" aria-label="Previous picture">‹</button><button data-lb-next class="lb-nav right-2" aria-label="Next picture">›</button>` : ''}
      </div>
      <div class="lb-bar flex flex-wrap items-center justify-center gap-2 text-xs text-white/80 max-w-[92vw]">
        <span class="hud-num text-white/60">${it?.upload ? `${esc(it.name)} · ` : ''}${it ? esc(fmtWhen(it.created_at)) : esc(title || '')}${many ? ` · ${i + 1} / ${items.length}` : ''}</span>
        ${it?.message?.text ? `<span class="max-w-md truncate text-white/70" title="${esc(it.message.text)}">“${esc(it.message.text)}”</span>` : ''}
        <a href="${esc(url)}" download="${esc(name)}" class="lb-btn">Save</a>
        ${it && !it.upload ? (it.avatar ? '<span class="lb-btn opacity-70 cursor-default">Profile picture</span>' : `<button data-lb-avatar class="lb-btn">Use as profile picture</button>`) : ''}
        <button data-lb-close class="lb-btn" aria-label="Close">✕ Close</button>
      </div>`;
  };
  const step = d => { if (!list || items.length < 2) return; i = (i + d + items.length) % items.length; url = items[i].url; draw(); animate(box.querySelector('[data-lb-img]'), d > 0 ? 'lb-next' : 'lb-prev'); };
  const close = () => { animateOut([box], 'fade-out', 160).then(() => box.remove()); document.removeEventListener('keydown', onKey, true); };
  const onKey = e => {
    if (e.key === 'Escape') { e.stopPropagation(); close(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
  };
  box.addEventListener('click', async e => {
    if (e.target === box || e.target.closest('[data-lb-close]')) return close();
    if (e.target.closest('[data-lb-prev]')) return step(-1);
    if (e.target.closest('[data-lb-next]')) return step(1);
    if (e.target.closest('[data-lb-avatar]') && await setAvatar(item().name)) { draw(); onChange?.(); }
  });
  draw();
  document.addEventListener('keydown', onKey, true);
  document.body.appendChild(box);
  animate(box, 'fade-in');
  animate(box.querySelector('.lb-frame'), 'zoom-in');
  return box;
}

// The agent's picture opens its profile picture; with none set yet, the gallery to pick one.
async function openProfilePicture() {
  try { await loadGallery(); } catch { /* still try the picture */ }
  const ok = await fetch(avatarURL(), { method: 'GET' }).then(r => r.ok).catch(() => false);
  if (!ok) { toast(`${chatName()} has no profile picture yet: pick one from the gallery.`, 'info'); return openGallery(); }
  const it = gal.items.find(x => x.avatar);
  const box = openLightbox(it ? it.url : avatarURL(), { title: `${chatName()}’s profile picture` });
  box.querySelector('.lb-bar').insertAdjacentHTML('beforeend', '<button data-lb-gallery class="lb-btn">Open the gallery</button>');
  box.addEventListener('click', e => { if (e.target.closest('[data-lb-gallery]')) { box.querySelector('[data-lb-close]').click(); openGallery(); } });
}

document.addEventListener('click', e => {
  if (e.target.closest('[data-agent-avatar]')) { e.preventDefault(); e.stopPropagation(); openProfilePicture(); }
  else if (e.target.closest('[data-agent-gallery]')) { e.preventDefault(); openGallery(); }
}, true);

loadGallery().catch(() => {});
