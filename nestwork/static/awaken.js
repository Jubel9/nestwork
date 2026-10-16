// The awakening page (#/awaken) and the appearance tables shared with Settings. Uses helpers from app.js.

// ---------------------------------------------------------------- units (cm/kg ⇄ in/lbs)
const UNIT_CONVERT = {
  cm: { imperial: 'in', to: v => v / 2.54, back: v => v * 2.54 },
  kg: { imperial: 'lbs', to: v => v * 2.20462, back: v => v / 2.20462 },
};
const UNIT_LABEL = { years: 'yrs', count: '', '%': '%', '/10': '/ 10' };
const units = () => localGet('nestwork.units') === 'imperial' ? 'imperial' : 'metric';
const round1 = v => Math.round(v * 10) / 10;

function unitLabel(p) {
  if (!p.unit) return '';
  if (units() === 'imperial' && UNIT_CONVERT[p.unit]) return UNIT_CONVERT[p.unit].imperial;
  return UNIT_LABEL[p.unit] ?? p.unit;
}
function shownValue(p, v) {
  if (v == null || p.type !== 'number') return v ?? '';
  return units() === 'imperial' && UNIT_CONVERT[p.unit] ? round1(UNIT_CONVERT[p.unit].to(v)) : v;
}
function storedValue(p, raw) {
  if (p.type !== 'number') return raw.trim() || null;
  if (raw === '' || raw == null) return null;
  const v = Number(raw);
  if (Number.isNaN(v)) return null;
  return units() === 'imperial' && UNIT_CONVERT[p.unit] ? round1(UNIT_CONVERT[p.unit].back(v)) : v;
}
const feetInches = cm => { const i = Math.round(cm / 2.54); return `${Math.floor(i / 12)}′${i % 12}″`; };

// ---------------------------------------------------------------- photos
const PHOTO_KINDS = [
  ['face', 'Face', 'A clear portrait'],
  ['body', 'Body', 'Full body, head to toe'],
  ['workspace', 'Workspace', 'Where your agent works'],
];

// Downscale before upload (long side 1600 px) so photos stay small and within Claude's image limits.
async function imageToDataUrl(file, max = 1600) {
  const img = await createImageBitmap(file);
  const scale = Math.min(1, max / Math.max(img.width, img.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(img.width * scale);
  canvas.height = Math.round(img.height * scale);
  canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.9);
}

const fileToDataUrl = file => new Promise((ok, fail) => {
  const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = fail; r.readAsDataURL(file);
});

// The face/body tile badge: how the photo passed the AI-generated check (see appearance.require_ai).
function verifiedBadge(v) {
  if (!v) return '';
  const [cls, label, tip] = !v.ok
    ? ['bg-blush-300', '✕ Not verified', 'This photo hasn’t passed the AI-generated check: upload it again. Selfies won’t use it.']
    : v.method === 'c2pa-trusted' || v.method === 'c2pa'
      ? ['bg-mint-300', `✓ Verified · ${v.by}`, `Content Credentials from a signer on the C2PA trust list prove ${v.by} generated it.`]
      : v.method === 'confirmed' || v.method === 'c2pa-self-signed'
        ? ['bg-mint-300', '✓ Verified',
           `You confirmed it shows an AI-generated character${v.by ? ` (its Content Credentials say ${v.by} made it)` : ''}.`]
        : ['bg-mint-300', `✓ Same character · ${v.confidence}/10`, `Matches your original: ${v.reason || ''}`];
  return `<span title="${esc(tip)}" class="absolute top-1.5 left-1.5 z-10 max-w-[calc(100%-12px)] truncate rounded-full ${cls} text-black text-[10px] font-bold px-2 py-0.5 shadow">${esc(label)}</span>`;
}

function photoTilesHTML(images, compact = false, verification = {}) {
  return `<div class="grid grid-cols-3 gap-3">${PHOTO_KINDS.map(([k, label, hint]) => `
    <label data-photo="${k}" class="photo-tile group relative block cursor-pointer rounded-2xl border-2 border-dashed border-lavender-200 bg-lavender-50/50 hover:border-lavender-300 overflow-hidden ${compact ? 'aspect-square' : 'aspect-[3/4]'}">
      <input type="file" accept="image/*" class="sr-only">
      ${images[k]
        ? `<img src="${images[k]}" alt="${label}" class="absolute inset-0 w-full h-full object-cover">${verifiedBadge(verification[k])}
           <span class="absolute bottom-0 inset-x-0 bg-black/50 text-white text-xs font-semibold px-2 py-1 flex justify-between"><span>${label}</span><span class="opacity-0 group-hover:opacity-100">Replace</span></span>`
        : `<span class="absolute inset-0 flex flex-col items-center justify-center text-center p-2">
             <span class="text-2xl">${k === 'face' ? '🙂' : k === 'body' ? '🧍' : '🪴'}</span>
             <span class="mt-1 text-sm font-semibold text-slate-700">${label}</span>
             ${compact ? '' : `<span class="text-[11px] text-slate-400">${hint}</span><span class="mt-2 text-[11px] text-lavender-700">Click or drop a photo</span>`}
           </span>`}
      <span class="uploading hidden absolute inset-0 bg-white/70 items-center justify-center text-xs text-slate-600">Uploading…</span>
    </label>`).join('')}</div>`;
}

// The confirmation for an original face nothing else can vouch for (appearance.CONFIRMATION): resolves true only
// when the box is ticked and Upload is pressed.
function confirmAiClaim(detail) {
  return new Promise(resolve => {
    let done = false;
    const finish = ok => { if (!done) { done = true; resolve(ok); } };
    openModal(`
      <h2 class="text-lg font-bold text-slate-800">Confirm this is an AI-generated character</h2>
      <p class="mt-2 text-sm text-slate-600">${esc(detail.message)}</p>
      <label class="mt-4 flex items-start gap-2 rounded-xl border border-butter-200 bg-butter-50 p-3 text-sm text-slate-800">
        <input type="checkbox" class="ai-confirm mt-0.5 shrink-0"><span>${esc(detail.confirm)}</span>
      </label>
      <div class="mt-5 flex justify-end gap-2">
        <button data-close class="rounded-xl px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-100">Cancel</button>
        <button class="ai-confirm-ok rounded-xl bg-lavender-700 hover:bg-lavender-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50 disabled:cursor-not-allowed" disabled title="Tick the confirmation first">Confirm and upload</button>
      </div>`, m => {
      const box = m.querySelector('.ai-confirm'), ok = m.querySelector('.ai-confirm-ok');
      box.onchange = () => { ok.disabled = !box.checked; };
      ok.onclick = () => { finish(box.checked); closeModal(); };
      new MutationObserver((_, obs) => { if (!m.isConnected) { obs.disconnect(); finish(false); } })
        .observe(modalRoot, { childList: true });
    });
  });
}

function bindPhotoTiles(root, onUploaded) {
  root.querySelectorAll('[data-photo]').forEach(tile => {
    const upload = async file => {
      if (!file?.type.startsWith('image/')) return toast('Choose an image file.', 'error');
      tile.querySelector('.uploading').textContent = ['face', 'body'].includes(tile.dataset.photo)
        ? 'Checking it’s AI generated…' : 'Uploading…';
      tile.querySelector('.uploading').classList.replace('hidden', 'flex');
      try {
        // Face and body go up untouched: re-encoding would strip the C2PA Content Credentials the server checks.
        const original = ['face', 'body'].includes(tile.dataset.photo);
        if (original && file.size > 8 * 1024 * 1024) throw new Error('That image is too large (8 MB max).');
        const dataUrl = original ? await fileToDataUrl(file) : await imageToDataUrl(file);
        const url = `/api/agent/images/${tile.dataset.photo}`;
        let payload;
        try {
          payload = await api('POST', url, { data_url: dataUrl });
        } catch (e) {
          // An original face without proof from a trusted signer: verified by the user's confirmation.
          if (e.status !== 409 || !e.detail?.confirm || !await confirmAiClaim(e.detail)) throw e;
          payload = await api('POST', url, { data_url: dataUrl, confirm_ai: true });
        }
        onUploaded(payload, tile.dataset.photo);
        // A new original face: offer to swap in an enhanced version, checked against it as the same character.
        const rec = payload.appearance.verified?.[tile.dataset.photo];
        if (tile.dataset.photo === 'face' && ['c2pa-trusted', 'confirmed'].includes(rec?.method)) {
          toast(`Original face verified${rec.method === 'confirmed' ? ' with your confirmation' : ` (made by ${rec.by})`}. Have an enhanced version of it? It’ll be checked to be the same character.`,
                'success', { label: 'Upload enhanced', run: () => root.querySelector('[data-photo="face"] input').click() });
        }
      } catch (e) {
        toast(e.message, 'error');
        tile.querySelector('.uploading').classList.replace('flex', 'hidden');
      }
    };
    tile.querySelector('input').onchange = e => upload(e.target.files[0]);
    tile.addEventListener('dragover', e => { e.preventDefault(); tile.classList.add('border-lavender-300', 'bg-lavender-100'); });
    tile.addEventListener('dragleave', () => tile.classList.remove('border-lavender-300', 'bg-lavender-100'));
    tile.addEventListener('drop', e => { e.preventDefault(); upload(e.dataTransfer.files[0]); });
  });
}

// ---------------------------------------------------------------- appearance tables
const TABLE_TITLES = { face: '🙂 Face', body: '🧍 Body', workspace: '🪴 Workspace' };

function appearanceTablesHTML(payload, { columns = 'lg:grid-cols-3' } = {}) {
  const a = payload.appearance, defs = payload.definitions[a.sex];
  const table = kind => `
    <div class="rounded-2xl border border-slate-200 overflow-hidden">
      <div class="flex items-center gap-2 bg-lavender-50 px-3 py-2">
        ${payload.images[kind] ? `<img src="${payload.images[kind]}" alt="" class="w-8 h-8 rounded-lg object-cover">` : ''}
        <span class="text-sm font-bold text-slate-700">${TABLE_TITLES[kind]}</span>
      </div>
      <table class="w-full text-sm">
        <thead><tr class="text-left text-[11px] uppercase tracking-wider text-slate-400">
          <th class="px-3 py-1.5 font-semibold">Parameter</th><th class="px-2 py-1.5 font-semibold">Value</th><th class="px-2 py-1.5 font-semibold w-12">Unit</th></tr></thead>
        <tbody>${defs[kind].map(p => {
          const v = a[kind][p.key];
          return `<tr class="border-t border-slate-100">
            <td class="px-3 py-1 text-slate-600">${esc(p.label)}</td>
            <td class="px-2 py-1"><input data-param="${kind}:${p.key}" ${p.type === 'number' ? 'type="number" step="0.1" inputmode="decimal"' : 'type="text"'}
              value="${esc(shownValue(p, v))}" placeholder="${esc(p.hint || '—')}"
              class="w-full min-w-0 rounded-md border border-transparent bg-transparent hover:border-slate-200 focus:border-lavender-300 focus:bg-white px-1.5 py-0.5 ${p.type === 'number' ? 'text-right tabular-nums' : ''} focus:outline-none"></td>
            <td class="px-2 py-1 text-xs text-slate-400 whitespace-nowrap">${unitLabel(p)}${p.key === 'height' && units() === 'imperial' && v ? ` <span class="text-slate-300">(${feetInches(v)})</span>` : ''}</td>
          </tr>`;
        }).join('')}</tbody>
      </table>
    </div>`;
  return `
    <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
      <div class="inline-flex rounded-full border border-slate-200 p-0.5 text-xs" role="group" aria-label="Units">
        ${[['metric', 'cm · kg'], ['imperial', 'in · lbs']].map(([k, l]) => `<button data-units="${k}" class="rounded-full px-3 py-1 font-semibold ${units() === k ? 'bg-lavender-100 text-lavender-800' : 'text-slate-500'}">${l}</button>`).join('')}
      </div>
      <span class="text-[11px] text-slate-400">${a.analyzed_at ? `Analysed ${esc(a.analyzed_at)} · ` : ''}Click any value to correct it</span>
    </div>
    <div class="grid ${columns} gap-3 items-start">${['face', 'body', 'workspace'].map(table).join('')}</div>`;
}

// Wires a container that holds appearanceTablesHTML: unit switch redraws, edits save themselves.
function bindAppearanceTables(box, getPayload, setPayload) {
  box.onclick = e => {
    const u = e.target.closest('[data-units]');
    if (!u) return;
    localSet('nestwork.units', u.dataset.units);
    box.innerHTML = appearanceTablesHTML(getPayload(), box._opts);
  };
  box.onchange = async e => {
    const input = e.target.closest('[data-param]');
    if (!input) return;
    const [kind, key] = input.dataset.param.split(':');
    const p = getPayload().definitions[getPayload().appearance.sex][kind].find(d => d.key === key);
    try {
      const payload = await api('PUT', '/api/agent/appearance', { [kind]: { [key]: storedValue(p, input.value) } });
      setPayload(payload);
      animate(input, 'fade-in');
    } catch (err) { toast(err.message, 'error'); }
  };
}

function renderTablesInto(box, payload, opts = {}) {
  box._opts = opts;
  box.innerHTML = appearanceTablesHTML(payload, opts);
}

// Start the appearance analysis (it runs on the server, so closing this doesn't stop it).
async function runAnalysis(button, statusEl) {
  try {
    return await watchAnalysis(await api('POST', '/api/agent/analyze', {}), button, statusEl);
  } catch (e) {
    toast(e.message, 'error');
    return null;
  }
}

// While an analysis runs (started now or earlier), show it and wait for the result.
async function watchAnalysis(payload, button, statusEl) {
  if (!payload.analyzing) return payload;
  const lines = ['Studying the face…', 'Estimating height and build…', 'Measuring proportions…', 'Looking around the workspace…', 'Writing it all down…'];
  let i = 0;
  button.disabled = true;
  statusEl.textContent = lines[0];
  const timer = setInterval(() => { statusEl.textContent = lines[++i % lines.length]; animate(statusEl, 'fade-in'); }, 4000);
  try {
    while (payload.analyzing) {
      await new Promise(r => setTimeout(r, 2000));
      if (!document.body.contains(statusEl)) return null;   // this view was closed; the job carries on
      payload = await api('GET', '/api/awakening');
    }
    if (payload.analysis_error) toast(`The analysis failed: ${payload.analysis_error}`, 'error');
    return payload;
  } finally {
    clearInterval(timer);
    button.disabled = false;
    statusEl.textContent = '';
  }
}

// Settings → Agent: the appearance section (photos, body type, tables, re-analyse).
function appearanceSectionHTML() {
  return `
    <section class="rounded-2xl border border-slate-200 p-4" id="st-appearance">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div><h3 class="text-sm font-bold text-slate-700">🪞 Appearance</h3>
          <p class="text-xs text-slate-500">From the face, body and workspace photos. Your agent knows this about itself.</p></div>
        <a href="#/awaken" data-close class="shrink-0 rounded-full border border-lavender-200 px-3 py-1 text-xs text-lavender-700 hover:bg-lavender-50">✦ Run the awakening again</a>
      </div>
      <div class="mt-3 grid sm:grid-cols-[14rem,1fr] gap-4 items-start">
        <div>
          <div class="ap-photos"></div>
          <div class="ap-remove mt-2 flex flex-wrap items-center gap-1.5 text-xs"></div>
          <div class="mt-2 inline-flex rounded-full border border-slate-200 p-0.5 text-xs">
            ${['female', 'male'].map(s => `<button data-sex="${s}" class="rounded-full px-3 py-1 font-semibold">${s === 'female' ? '♀ Female' : '♂ Male'}</button>`).join('')}
          </div>
          <button class="ap-analyze mt-2 block w-full rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-semibold px-3 py-1.5 disabled:opacity-50">✦ Analyse photos</button>
          <p class="ap-status mt-1 text-[11px] text-lavender-700 h-4"></p>
        </div>
        <div class="ap-tables min-w-0"></div>
      </div>
    </section>`;
}

function bindAppearanceSection(root, initial) {
  const sec = root.querySelector('#st-appearance');
  if (!sec) return;
  let payload = initial;
  const draw = () => {
    sec.querySelector('.ap-photos').innerHTML = photoTilesHTML(payload.images, true, payload.verification);
    bindPhotoTiles(sec, p => { payload = p; draw(); });
    // Remove a photo so it can be uploaded again (e.g. the original face, to verify it with the confirmation).
    const there = PHOTO_KINDS.filter(([k]) => payload.images[k]);
    const removeRow = sec.querySelector('.ap-remove');
    removeRow.innerHTML = there.length ? `<span class="text-slate-500">Remove:</span>${there.map(([k, label]) => `
      <button data-remove-photo="${k}" class="rounded-full border border-slate-200 px-2.5 py-0.5 text-slate-600 hover:border-blush-200 hover:bg-blush-50 hover:text-blush-700">✕ ${label}</button>`).join('')}` : '';
    removeRow.querySelectorAll('[data-remove-photo]').forEach(b => b.onclick = async () => {
      const k = b.dataset.removePhoto, label = PHOTO_KINDS.find(([x]) => x === k)[1].toLowerCase();
      const extra = k === 'face' ? ' Its original (the anchor your enhanced face and body were checked against) goes too, so the next face you upload is checked as a new original.' : '';
      if (!confirm(`Remove the ${label} photo?${extra} Daily backups don’t include photos: use Back up now (Trash & backups) first if you may want it back.`)) return;
      try {
        payload = await api('DELETE', `/api/agent/images/${k}`);
        draw();
        toast(`The ${label} photo was removed. Upload a new one above.`, 'success');
      } catch (e) { toast(e.message, 'error'); }
    });
    sec.querySelectorAll('[data-sex]').forEach(b => {
      const on = b.dataset.sex === payload.appearance.sex;
      b.className = `rounded-full px-3 py-1 font-semibold ${on ? 'bg-lavender-100 text-lavender-800' : 'text-slate-500'}`;
    });
    renderTablesInto(sec.querySelector('.ap-tables'), payload, { columns: '' });
  };
  bindAppearanceTables(sec.querySelector('.ap-tables'), () => payload, p => { payload = p; });
  sec.querySelectorAll('[data-sex]').forEach(b => b.onclick = async () => {
    payload = await api('PUT', '/api/agent/appearance', { sex: b.dataset.sex }); draw();
  });
  sec.querySelector('.ap-analyze').onclick = async e => {
    const p = await runAnalysis(e.currentTarget, sec.querySelector('.ap-status'));
    if (p) { payload = p; draw(); if (!p.analysis_error) toast('Appearance analysed.', 'success'); }
  };
  draw();
  watchAnalysis(payload, sec.querySelector('.ap-analyze'), sec.querySelector('.ap-status'))
    .then(p => { if (p && p !== initial) { payload = p; draw(); } });
}

// ---------------------------------------------------------------- wardrobe (Settings → Agent)
function wardrobeSectionHTML() {
  return `
    <section class="rounded-2xl border border-slate-200 p-4" id="st-wardrobe">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div><h3 class="text-sm font-bold text-slate-700">👗 Wardrobe</h3>
          <p class="text-xs text-slate-500">Add photos of clothes; each is analysed when you add it. Click an item to put it on or take it off.</p></div>
        <div class="inline-flex rounded-full border border-slate-200 p-0.5 text-xs" role="group" aria-label="Who picks the outfit">
          <button data-wmode="user" class="rounded-full px-3 py-1 font-semibold">You pick</button>
          <button data-wmode="agent" class="rounded-full px-3 py-1 font-semibold">Agent picks</button>
        </div>
      </div>
      <p class="wd-outfit mt-2 text-xs text-slate-600"></p>
      <div class="wd-grid mt-3 grid grid-cols-2 sm:grid-cols-4 gap-3"></div>
      <div class="wd-detail"></div>
    </section>`;
}

function bindWardrobeSection(root, initial) {
  const sec = root.querySelector('#st-wardrobe');
  if (!sec) return;
  let w = initial, open = null;
  const busy = new Set();   // uploads still on their way to the server
  const card = i => {
    const p = i.params;
    const nums = [['formality', 'Formal'], ['warmth', 'Warm'], ['comfort', 'Comfy']].filter(([k]) => p[k] != null)
      .map(([k, l]) => `<span class="rounded-full bg-slate-100 px-1.5">${l} ${p[k]}/10</span>`).join('');
    return `
      <div class="wd-item group relative rounded-2xl border-2 ${i.worn ? 'border-mint-300 ring-2 ring-mint-100' : 'border-slate-200'} overflow-hidden bg-white" data-item="${i.id}">
        <button data-wear class="block w-full aspect-square bg-slate-50" title="${i.worn ? 'Take it off' : 'Put it on'}">
          ${i.image ? `<img src="${i.image}" alt="" class="w-full h-full object-cover">` : ''}
          ${i.worn ? '<span class="absolute top-1.5 left-1.5 rounded-full bg-mint-300 text-slate-900 text-[10px] font-bold px-2 py-0.5">Wearing</span>' : ''}
          ${i.analyzing ? '<span class="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-white/75 text-xs text-slate-600"><span class="typing-dots text-lg leading-none"><span>•</span><span>•</span><span>•</span></span>Analysing…</span>'
            : !i.analyzed ? `<span class="absolute inset-0 flex items-center justify-center bg-white/75 text-xs text-blush-700 px-2 text-center">${i.analysis_error ? 'Analysis failed: try Re-analyse' : 'Not analysed yet'}</span>` : ''}
        </button>
        <div class="p-2">
          <p class="text-xs font-semibold text-slate-700 truncate">${esc(p.name || 'Clothing')}</p>
          <p class="text-[10px] text-slate-400 truncate">${esc([p.category, p.colors].filter(Boolean).join(' · '))}</p>
          <div class="mt-1 flex flex-wrap gap-1 text-[10px] text-slate-500">${nums}</div>
          <div class="mt-1.5 flex gap-2 text-[11px]">
            <button data-details class="text-lavender-700 hover:underline">${open === i.id ? 'Hide' : 'Details'}</button>
            <button data-reanalyze class="text-slate-500 hover:underline">Re-analyse</button>
            <button data-remove class="ml-auto text-blush-700 hover:underline">Remove</button>
          </div>
        </div>
      </div>`;
  };
  const detail = i => {
    return `<div class="mt-3 rounded-2xl border border-slate-200 overflow-hidden" data-detail="${i.id}">
      <div class="bg-lavender-50 px-3 py-2 text-sm font-bold text-slate-700">${esc(i.params.name || 'Clothing')}: details</div>
      <table class="w-full text-sm"><tbody>${w.definitions.map(p => `
        <tr class="border-t border-slate-100"><td class="px-3 py-1 text-slate-600">${esc(p.label)}</td>
        <td class="px-2 py-1"><input data-cparam="${p.key}" ${p.type === 'number' ? 'type="number" step="0.1"' : 'type="text"'} value="${esc(shownValue(p, i.params[p.key]))}" placeholder="—"
          class="w-full rounded-md border border-transparent bg-transparent hover:border-slate-200 focus:border-lavender-300 focus:bg-white px-1.5 py-0.5 ${p.type === 'number' ? 'text-right tabular-nums' : ''} focus:outline-none"></td>
        <td class="px-2 py-1 text-xs text-slate-400 w-12">${unitLabel(p)}</td></tr>`).join('')}</tbody></table></div>`;
  };
  const draw = () => {
    sec.querySelectorAll('[data-wmode]').forEach(b => b.className = `rounded-full px-3 py-1 font-semibold ${b.dataset.wmode === w.mode ? 'bg-lavender-100 text-lavender-800' : 'text-slate-500'}`);
    const who = w.changed_by && w.changed_at ? ` <span class="text-slate-400">(changed by ${esc(w.changed_by)} · ${esc(w.changed_at)})</span>` : '';
    sec.querySelector('.wd-outfit').innerHTML = `<b>Wearing now:</b> ${esc(w.outfit)}${who}
      ${w.items.some(i => i.worn) ? ' <button data-nothing class="ml-1 text-lavender-700 underline">No outfit</button>' : ''}
      ${w.mode === 'agent' ? '<span class="block text-slate-400">Give the agent agency to select the clothes.</span>' : ''}`;
    sec.querySelector('.wd-grid').innerHTML = w.items.map(card).join('') + [...busy]
      .map(() => '<div class="rounded-2xl border-2 border-dashed border-lavender-200 aspect-square flex items-center justify-center text-xs text-slate-500">Uploading…</div>').join('') + `
      <label class="relative rounded-2xl border-2 border-dashed border-lavender-200 bg-lavender-50/50 hover:border-lavender-300 aspect-square flex flex-col items-center justify-center text-center cursor-pointer">
        <input type="file" accept="image/*" multiple class="sr-only wd-file">
        <span class="text-2xl">👗</span><span class="text-xs font-semibold text-slate-700 mt-1">Add clothes</span><span class="text-[10px] text-slate-400">Click or drop photos</span>
      </label>`;
    const item = w.items.find(i => i.id === open);
    sec.querySelector('.wd-detail').innerHTML = item ? detail(item) : '';
  };
  const upload = async files => {
    for (const file of [...files].filter(f => f.type.startsWith('image/'))) {
      const tag = `new-${Math.random()}`;
      busy.add(tag); draw();
      try {
        w = await api('POST', '/api/agent/wardrobe', { data_url: await imageToDataUrl(file, 1200) });
      } catch (e) { toast(e.message, 'error'); }
      busy.delete(tag); draw(); watch();
    }
  };
  // While anything is being analysed (even from before Settings was reopened), check every 2 s.
  let watching = false;
  const watch = async () => {
    if (watching) return;
    watching = true;
    while (w.items.some(i => i.analyzing) && document.body.contains(sec)) {
      await new Promise(r => setTimeout(r, 2000));
      if (!document.body.contains(sec)) break;
      const before = new Map(w.items.map(i => [i.id, i.analyzing]));
      w = await api('GET', '/api/agent/wardrobe');
      w.items.filter(i => before.get(i.id) && !i.analyzing).forEach(i => {
        if (i.analysis_error) toast(`Couldn’t analyse a clothing photo: ${i.analysis_error}`, 'error');
        draw(); animate(sec.querySelector(`[data-item="${i.id}"]`), 'land');
      });
    }
    watching = false;
  };
  sec.addEventListener('change', e => { if (e.target.matches('.wd-file')) upload(e.target.files); });
  sec.addEventListener('dragover', e => e.preventDefault());
  sec.addEventListener('drop', e => { e.preventDefault(); upload(e.dataTransfer.files); });
  sec.addEventListener('click', async e => {
    const mode = e.target.closest('[data-wmode]');
    if (mode) { w = await api('PUT', '/api/agent/wardrobe/mode', { mode: mode.dataset.wmode }); return draw(); }
    if (e.target.closest('[data-nothing]')) { w = await api('PUT', '/api/agent/wardrobe/outfit', { item_ids: [] }); return draw(); }
    const el = e.target.closest('[data-item]');
    if (!el) return;
    const id = Number(el.dataset.item), item = w.items.find(i => i.id === id);
    if (e.target.closest('[data-wear]')) {
      w = await api('PUT', `/api/agent/wardrobe/${id}`, { worn: !item.worn });
      draw(); animate(sec.querySelector(`[data-item="${id}"]`), 'land');
    } else if (e.target.closest('[data-details]')) {
      open = open === id ? null : id; draw();
    } else if (e.target.closest('[data-reanalyze]')) {
      try { w = await api('POST', `/api/agent/wardrobe/${id}/analyze`, {}); } catch (err) { toast(err.message, 'error'); }
      draw(); watch();
    } else if (e.target.closest('[data-remove]')) {
      if (!confirm(`Remove “${item.params.name || 'this item'}” from the wardrobe?`)) return;
      await animateOut([el], 'poof', 200);
      w = await api('DELETE', `/api/agent/wardrobe/${id}`); if (open === id) open = null; draw();
    }
  });
  sec.addEventListener('change', async e => {
    const input = e.target.closest('[data-cparam]');
    if (!input || open == null) return;
    const p = w.definitions.find(d => d.key === input.dataset.cparam);
    w = await api('PUT', `/api/agent/wardrobe/${open}`, { params: { [p.key]: storedValue(p, input.value) } });
    draw();
  });
  draw();
  watch();
}

// ---------------------------------------------------------------- pictures (Settings → Agent)
// Provider + model for drawings and for selfies, and the Claude model that writes selfie prompts.
function picturesSectionHTML() {
  return `
    <section class="rounded-2xl border border-slate-200 p-4" id="st-pictures">
      <h3 class="text-sm font-bold text-slate-700">🖼️ Pictures</h3>
      <p class="text-xs text-slate-500">Your agent can draw pictures and send selfies in the chat. API keys go in the <code>.env</code> file; choose providers and models here.</p>
      <div class="pc-keys mt-2 flex flex-wrap gap-2 text-xs"></div>
      <div class="mt-3 grid sm:grid-cols-2 gap-3">
        ${[['image', '🎨 Drawings'], ['selfie', '🤳 Selfies']].map(([k, title]) => `
          <div class="rounded-xl bg-slate-50 border border-slate-200 p-3 space-y-2">
            <p class="text-xs font-bold text-slate-600">${title}</p>
            <label class="block"><span class="block text-[11px] font-semibold text-slate-500 mb-0.5">Provider</span>
              <select data-pic="${k}_provider" class="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm"></select></label>
            <label class="block"><span class="block text-[11px] font-semibold text-slate-500 mb-0.5">Model</span>
              <span class="pc-model block" data-for="${k}"></span></label>
            ${k === 'selfie' ? `<label class="block"><span class="block text-[11px] font-semibold text-slate-500 mb-0.5">Prompt writer (Claude)</span>
              <span class="pc-writer block"></span></label>` : ''}
          </div>`).join('')}
      </div>
      <p class="mt-2 text-[11px] text-slate-400">Selfies use your face, body, worn clothes and workspace photos as references, and need an outfit on.</p>
    </section>`;
}

async function bindPicturesSection(root, settings) {
  const sec = root.querySelector('#st-pictures');
  if (!sec) return;
  const info = await api('GET', '/api/imagegen');
  const cls = 'w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm';
  sec.querySelector('.pc-keys').innerHTML = Object.entries(info.providers).map(([p, name]) => info.keys[p]
    ? `<span class="rounded-full bg-mint-100 text-mint-700 px-2 py-0.5">✓ ${name} key found</span>`
    : `<span class="rounded-full bg-butter-100 text-butter-700 px-2 py-0.5" title="${esc(info.env_file)}">${name}: add <code>${info.key_names[p]}=…</code> to .env</span>`).join('');
  const save = patch => attempt(() => api('PUT', '/api/settings', patch)).then(r => { if (r) Object.assign(settings, r); });
  const drawModel = async kind => {
    const provider = settings[`${kind}_provider`];
    const { models, live } = await api('GET', `/api/imagegen/models?provider=${provider}`);
    const box = sec.querySelector(`.pc-model[data-for="${kind}"]`);
    box.innerHTML = fieldControl(`data-pic="${kind}_model"`, { options: models }, settings[`${kind}_model`], cls)
      + `<span class="block mt-0.5 text-[10px] text-slate-400">${live ? 'Live list from OpenRouter' : 'Suggestions; “Other…” takes any model name'}</span>`;
  };
  for (const kind of ['image', 'selfie']) {
    const sel = sec.querySelector(`[data-pic="${kind}_provider"]`);
    sel.innerHTML = Object.entries(info.providers).map(([p, name]) => `<option value="${p}" ${settings[`${kind}_provider`] === p ? 'selected' : ''}>${name}${info.keys[p] ? '' : ' (no key yet)'}</option>`).join('');
    drawModel(kind);
  }
  sec.querySelector('.pc-writer').innerHTML = modelSelect('pc-writer', settings.selfie_prompt_model, cls);
  sec.addEventListener('change', async e => {
    const key = e.target.dataset.pic || (e.target.id === 'pc-writer' ? 'selfie_prompt_model' : null);
    if (!key || e.target.value === OTHER) return;
    await save({ [key]: e.target.value });
    if (key.endsWith('_provider')) {
      const kind = key.split('_')[0];
      settings[`${kind}_model`] = '';
      drawModel(kind);
    }
  });
}

// ---------------------------------------------------------------- the awakening page
const AWAKEN_STEPS = ['Welcome', 'About you', 'Your agent', 'Appearance', 'Abilities', 'Awaken'];
const awakening = { step: 0, data: null };

async function renderAwaken() {
  document.body.classList.add('awakening');
  document.title = 'Awaken your agent · Nestwork';
  const [files, settings, look] = await Promise.all([api('GET', '/api/agent/files'), api('GET', '/api/settings'), api('GET', '/api/awakening')]);
  awakening.data = { files, settings, look };
  enterPage('awaken', 1);
  drawAwakenStep(true);
}
window.renderAwaken = renderAwaken;

function fieldGrid(block, defs, values) {
  return `<div class="grid sm:grid-cols-2 gap-4">${defs.map(f => `
    <label class="block">
      <span class="block text-sm font-semibold text-slate-700 mb-1">${esc(f.label)}</span>
      ${fieldControl(`data-field="${block}:${f.key}"`, f, values[f.key] || '', 'w-full rounded-xl border border-slate-300 bg-white/80 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-lavender-300')}
    </label>`).join('')}</div>`;
}

function choiceCards(name, choices, current, cols = 'sm:grid-cols-3') {
  return `<div class="grid ${cols} gap-2">${choices.map(([k, title, help]) => `
    <label class="flex items-start gap-3 rounded-2xl border p-3 cursor-pointer bg-white/70 ${current === k ? 'border-lavender-300 ring-2 ring-lavender-100' : 'border-slate-200 hover:bg-white'}">
      <input type="radio" name="${name}" value="${k}" ${current === k ? 'checked' : ''} class="mt-1 text-lavender-700">
      <span><span class="block text-sm font-semibold">${title}</span>${help ? `<span class="block text-xs text-slate-500">${help}</span>` : ''}</span>
    </label>`).join('')}</div>`;
}

function awakenStepHTML() {
  const { files, settings, look } = awakening.data;
  const agentName = files.fields.persona.name || 'your agent';
  switch (awakening.step) {
    case 0: return `
      <div class="text-center py-6">
        <div class="aw-orb mx-auto w-28 h-28 rounded-full bg-gradient-to-br from-blush-200 via-lilac-300 to-baby-300 flex items-center justify-center text-4xl shadow-[0_0_60px_rgb(var(--c-lilac-300)/.6)]">✦</div>
        <h1 class="mt-6 text-3xl font-extrabold text-slate-800">Let’s awaken your agent</h1>
        <p class="mt-3 text-slate-500 max-w-md mx-auto">In a few steps you’ll introduce yourself, shape who your agent is, show it what it looks like and where it works, and decide what it may do. Then it wakes up and says hello in its own words.</p>
      </div>`;
    case 1: return `
      <h2 class="text-2xl font-extrabold text-slate-800">🙋 About you</h2>
      <p class="text-sm text-slate-500 mt-1 mb-5">Shared with every Claude session on this computer, so they all know who you are.</p>
      ${fieldGrid('about', files.field_defs.about, files.fields.about)}`;
    case 2: return `
      <h2 class="text-2xl font-extrabold text-slate-800">✦ Your agent</h2>
      <p class="text-sm text-slate-500 mt-1 mb-5">Who it is. Only the Nestwork agent takes on this name and personality. Pick a suggestion or write your own.</p>
      ${fieldGrid('persona', files.field_defs.persona, files.fields.persona)}`;
    case 3: return `
      <h2 class="text-2xl font-extrabold text-slate-800">🪞 How ${esc(agentName)} looks</h2>
      <p class="text-sm text-slate-500 mt-1 mb-4">Add a face photo, a full-body photo and its workspace. Claude studies them and fills in the tables below; correct anything you like.</p>
      <div class="flex flex-wrap items-center gap-3 mb-4">
        <span class="text-sm font-semibold text-slate-700">Body type</span>
        <div class="inline-flex rounded-full border border-slate-200 bg-white/70 p-0.5 text-sm">
          ${['female', 'male'].map(s => `<button data-sex="${s}" class="rounded-full px-4 py-1 font-semibold ${look.appearance.sex === s ? 'bg-lavender-100 text-lavender-800' : 'text-slate-500'}">${s === 'female' ? '♀ Female' : '♂ Male'}</button>`).join('')}
        </div>
      </div>
      <div class="aw-photos max-w-xl"></div>
      <div class="mt-4 flex flex-wrap items-center gap-3">
        <button class="aw-analyze rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-semibold px-5 py-2 disabled:opacity-50">✦ Analyse photos</button>
        <span class="aw-status text-sm text-lavender-700"></span>
      </div>
      <div class="aw-tables mt-5"></div>`;
    case 4: return `
      <h2 class="text-2xl font-extrabold text-slate-800">🧰 What ${esc(agentName)} may do</h2>
      <p class="text-sm text-slate-500 mt-1 mb-5">You can change all of this later in Settings.</p>
      <h3 class="text-sm font-bold text-slate-700 mb-2">Tools it can use</h3>
      ${choiceCards('tools', TOOL_LEVELS, settings.agent_tools)}
      <h3 class="text-sm font-bold text-slate-700 mt-5 mb-2">Changes to your projects</h3>
      ${choiceCards('access', ACCESS, settings.agent_access)}
      <label class="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" id="aw-delete" ${settings.allow_delete ? 'checked' : ''} class="rounded text-blush-700"> Let it delete blocks</label>
      <h3 class="text-sm font-bold text-slate-700 mt-5 mb-2">Reminders</h3>
      ${choiceCards('reminder_style', [['quick', '⚡ Quick', 'Written by Nestwork: instant and free.'], ['agent', '✦ In its own words', 'Uses Claude for each reminder.']], settings.reminder_style, 'sm:grid-cols-2')}
      <div class="mt-2 text-sm flex items-center gap-2">Desktop notifications: <span id="aw-notif"></span></div>
      <div class="mt-5 grid sm:grid-cols-2 gap-5">
        <label class="block"><span class="block text-sm font-bold text-slate-700 mb-2">Model</span>
          ${modelSelect('aw-model', settings.agent_model, 'w-full rounded-xl border border-slate-300 bg-white/80 px-3 py-2')}</label>
        <div><span class="block text-sm font-bold text-slate-700 mb-2">Theme</span>
          <div class="inline-flex rounded-full border border-slate-200 bg-white/70 p-0.5 text-sm">${THEME_CHOICES.map(([k, l]) => `<button data-theme="${k}" class="rounded-full px-3 py-1 font-semibold ${theme === k ? 'bg-lavender-100 text-lavender-800' : 'text-slate-500'}">${l}</button>`).join('')}</div></div>
      </div>`;
    default: return `
      <div class="text-center py-4">
        <div class="flex justify-center gap-3 mb-6">${PHOTO_KINDS.map(([k]) => look.images[k]
          ? `<img src="${look.images[k]}" alt="" class="w-20 h-24 rounded-2xl object-cover shadow-md">`
          : `<span class="w-20 h-24 rounded-2xl border-2 border-dashed border-lavender-200"></span>`).join('')}</div>
        <h2 class="text-3xl font-extrabold text-slate-800">Ready to awaken ${esc(agentName)}?</h2>
        <p class="mt-3 text-slate-500 max-w-md mx-auto">${esc(agentName)} will open its eyes in the chat and greet ${esc(files.fields.about.name || 'you')} in its own words.</p>
        <button class="aw-awaken mt-8 rounded-full bg-gradient-to-r from-blush-300 via-lilac-300 to-baby-300 text-slate-900 font-extrabold text-lg px-10 py-3 shadow-lg hover:shadow-xl">✦ Awaken ${esc(agentName)}</button>
      </div>`;
  }
}

function drawAwakenStep(first = false) {
  const s = awakening.step, last = AWAKEN_STEPS.length - 1;
  app.innerHTML = `
    <div class="aw-page fixed inset-0 z-10 overflow-y-auto">
      <div class="aw-glow" aria-hidden="true"><span></span><span></span><span></span></div>
      <div class="relative min-h-full flex flex-col items-center px-4 py-8">
        <div class="w-full max-w-5xl flex items-center justify-between mb-6">
          <a href="#/" class="text-sm text-slate-500 hover:text-slate-800">← ${awakening.data.settings.awakened ? 'Back to Nestwork' : 'Skip for now'}</a>
          <ol class="hidden md:flex items-center gap-2 text-xs">${AWAKEN_STEPS.map((l, i) => `
            <li class="flex items-center gap-2"><button data-goto="${i}" class="rounded-full px-2.5 py-1 font-semibold ${i === s ? 'bg-lavender-700 text-white' : i < s ? 'bg-lavender-100 text-lavender-800' : 'text-slate-400'}">${i + 1}. ${l}</button>${i < last ? '<span class="text-slate-300">›</span>' : ''}</li>`).join('')}</ol>
          <span class="md:hidden text-xs text-slate-500">Step ${s + 1} of ${last + 1}</span>
        </div>
        <section class="aw-card w-full ${s === 3 ? 'max-w-5xl' : 'max-w-3xl'} rounded-3xl bg-white/85 backdrop-blur border border-lavender-100 shadow-xl p-6 sm:p-8">
          ${awakenStepHTML()}
          ${s < last ? `
          <div class="mt-8 flex items-center justify-between">
            ${s > 0 ? '<button data-back class="rounded-full px-4 py-2 text-slate-600 hover:bg-slate-100">← Back</button>' : '<span></span>'}
            <button data-next class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-semibold px-6 py-2">${s === 0 ? 'Begin ✦' : 'Continue →'}</button>
          </div>` : '<div class="mt-6 text-center"><button data-back class="text-sm text-slate-500 hover:text-slate-800">← Back</button></div>'}
        </section>
      </div>
    </div>`;
  const card = app.querySelector('.aw-card');
  animate(card, first ? 'zoom-in' : 'page-side');
  bindAwakenStep(card);
}

async function saveAwakenStep() {
  const card = app.querySelector('.aw-card');
  for (const block of ['about', 'persona']) {
    const inputs = [...card.querySelectorAll(`[data-field^="${block}:"]`)];
    if (!inputs.length) continue;
    const values = Object.fromEntries(inputs.map(i => [i.dataset.field.split(':')[1], i.value]));
    awakening.data.files = await api('PUT', `/api/agent/fields/${block}`, { values });
  }
}

function bindAwakenStep(card) {
  const { look } = awakening.data;
  const go2 = async step => {
    try { await saveAwakenStep(); } catch (e) { return toast(e.message, 'error'); }
    awakening.step = step;
    drawAwakenStep();
    app.querySelector('.aw-page').scrollTop = 0;
  };
  card.querySelector('[data-next]')?.addEventListener('click', () => go2(awakening.step + 1));
  card.querySelector('[data-back]')?.addEventListener('click', () => go2(awakening.step - 1));
  app.querySelectorAll('[data-goto]').forEach(b => b.onclick = () => go2(Number(b.dataset.goto)));
  card.querySelector('[data-field]')?.focus();

  if (awakening.step === 3) {
    const drawLook = () => {
      card.querySelector('.aw-photos').innerHTML = photoTilesHTML(awakening.data.look.images, false, awakening.data.look.verification);
      bindPhotoTiles(card, (p, kind) => { awakening.data.look = p; drawLook(); animate(card.querySelector(`[data-photo="${kind}"]`), 'land'); });
      renderTablesInto(card.querySelector('.aw-tables'), awakening.data.look);
    };
    bindAppearanceTables(card.querySelector('.aw-tables'), () => awakening.data.look, p => { awakening.data.look = p; });
    card.querySelectorAll('[data-sex]').forEach(b => b.onclick = async () => {
      awakening.data.look = await api('PUT', '/api/agent/appearance', { sex: b.dataset.sex });
      card.querySelectorAll('[data-sex]').forEach(x => x.className = `rounded-full px-4 py-1 font-semibold ${x === b ? 'bg-lavender-100 text-lavender-800' : 'text-slate-500'}`);
      drawLook();
    });
    const showResult = p => {
      if (!p || p === awakening.data.look) return;
      awakening.data.look = p; drawLook(); stagger(card.querySelectorAll('.aw-tables tr'), 'pop-in', 12, 500);
    };
    card.querySelector('.aw-analyze').onclick = async e => showResult(await runAnalysis(e.currentTarget, card.querySelector('.aw-status')));
    drawLook();
    watchAnalysis(awakening.data.look, card.querySelector('.aw-analyze'), card.querySelector('.aw-status')).then(showResult);
  }

  if (awakening.step === 4) {
    const s = awakening.data.settings;
    const save = async (patch) => { const r = await attempt(() => api('PUT', '/api/settings', patch)); if (r) Object.assign(s, r); };
    const repaint = name => card.querySelectorAll(`[name=${name}]`).forEach(i => {
      i.closest('label').className = `flex items-start gap-3 rounded-2xl border p-3 cursor-pointer bg-white/70 ${i.checked ? 'border-lavender-300 ring-2 ring-lavender-100' : 'border-slate-200 hover:bg-white'}`;
    });
    card.querySelectorAll('[name=tools]').forEach(r => r.onchange = () => { save({ agent_tools: r.value }); repaint('tools'); });
    card.querySelectorAll('[name=access]').forEach(r => r.onchange = () => { save({ agent_access: r.value }); repaint('access'); });
    card.querySelectorAll('[name=reminder_style]').forEach(r => r.onchange = () => { save({ reminder_style: r.value }); repaint('reminder_style'); });
    card.querySelector('#aw-delete').onchange = e => save({ allow_delete: e.target.checked });
    card.querySelector('#aw-model').onchange = e => save({ agent_model: e.target.value });
    card.querySelectorAll('[data-theme]').forEach(b => b.onclick = () => {
      setTheme(b.dataset.theme);
      card.querySelectorAll('[data-theme]').forEach(x => x.className = `rounded-full px-3 py-1 font-semibold ${x === b ? 'bg-lavender-100 text-lavender-800' : 'text-slate-500'}`);
    });
    const notif = card.querySelector('#aw-notif');
    const drawNotif = () => {
      if (!('Notification' in window)) notif.textContent = 'not supported here';
      else if (Notification.permission === 'granted') notif.innerHTML = '<span class="text-mint-700 font-semibold">on ✓</span>';
      else if (Notification.permission === 'denied') notif.textContent = 'blocked in your browser settings';
      else {
        notif.innerHTML = '<button class="rounded-full border border-lavender-200 px-3 py-0.5 text-lavender-700 hover:bg-lavender-50">Turn on</button>';
        notif.querySelector('button').onclick = async () => { await Notification.requestPermission(); drawNotif(); };
      }
    };
    drawNotif();
  }

  card.querySelector('.aw-awaken')?.addEventListener('click', async e => {
    e.currentTarget.disabled = true;
    if (!await attempt(() => api('POST', '/api/awaken'))) { e.currentTarget.disabled = false; return; }
    awakening.data.settings.awakened = true;
    app.querySelector('.aw-page').classList.add('aw-waking');
    await new Promise(r => setTimeout(r, 1400));
    document.body.classList.remove('awakening');
    location.hash = '#/';
    setTimeout(() => window.startAwakeningChat?.(), 400);
  });
}

// Leaving the page: show the normal header and chat again.
window.addEventListener('hashchange', () => {
  if (!location.hash.startsWith('#/awaken')) document.body.classList.remove('awakening');
});
