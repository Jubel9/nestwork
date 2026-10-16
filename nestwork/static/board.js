// The block page's views: nested bubbles, the board (phases as shared columns), the outline, the tree and
// "What can I do now?", plus the quick "new to-do" dialog. Uses app.js (state, api, icons, motion).

// ---------------------------------------------------------------- bubbles
// A block's contents drawn as nested bubbles. Real children sit inside their parent's bubble;
// an item that waits for a sibling sits inside that sibling's bubble (marked ↳), so chains
// like Backstory → 2D sprites → 3D model read from the outside in.
const BUBBLE_MAX_DEPTH = 6;
const BUBBLE_MAX_PER_LEVEL = 8;

function arrange(nodes) {
  const ids = new Set(nodes.map(n => n.id));
  const followers = new Map(nodes.map(n => [n.id, []]));
  const tops = [];
  for (const n of nodes) {
    const host = nodes.find(x => (n.after || []).includes(x.id));
    host && ids.has(host.id) ? followers.get(host.id).push(n) : tops.push(n);
  }
  return { tops, followers };
}

function bubblesHTML(nodes, depth = 0) {
  if (!nodes.length) return '';
  const { tops, followers } = arrange(nodes);
  const list = (items, isFollower) => items.map(n => bubbleHTML(n, depth, followers, isFollower));
  return groupHTML(list(tops, false));

  function bubbleHTML(n, d, followers, isFollower) {
    const st = STAGE_STYLE[n.status];
    const kids = n.children || [];
    const chained = followers.get(n.id) || [];
    const inner = d + 1 >= BUBBLE_MAX_DEPTH
      ? ((kids.length + chained.length) ? [`<span class="text-[11px] text-slate-400 px-1">+${kids.length + chained.length}</span>`] : [])
      : [
          ...(kids.length ? [bubblesHTML(kids, d + 1)] : []),
          ...chained.map(f => bubbleHTML(f, d + 1, followers, true)),
        ];
    const bg = d % 2 === 0 ? `bg-${st.color}-50` : 'bg-white/90';
    const border = n.locked ? `border-dashed border-${st.color}-300` : `border-${st.color}-200`;
    const mark = n.done ? '<span class="text-mint-700">✓</span>'
      : n.locked ? `<span class="text-butter-700" title="Starts when ${esc(n.blocked_by.join(' and '))}">${ICON.lock}</span>`
      : `<span class="w-1.5 h-1.5 rounded-full ${st.dot} shrink-0"></span>`;
    return `
      <div data-go="${n.id}" data-stage="${n.status}" title="${esc(n.title)} · ${esc(stageLabel(n.status))}${n.locked ? ' · starts when ' + esc(n.blocked_by.join(' and ')) : n.gate && !n.done ? ' · ' + esc(stageLabel(n.gate.stage)) + ' when ' + esc(n.gate.reasons.join(' and ')) : ''}"
           class="bubble max-w-full rounded-2xl border ${border} ${bg} px-2 py-1 cursor-pointer hover:shadow-sm hover:brightness-[0.98] transition">
        <div class="flex items-center gap-1 text-xs font-semibold ${st.head} min-w-0">
          ${isFollower ? '<span class="text-slate-400 font-normal">↳</span>' : ''}${mark}
          <span class="truncate ${n.done ? 'line-through decoration-mint-300' : ''}">${esc(n.title)}</span>
          ${kids.length ? `<span class="ml-auto pl-1 text-[10px] font-medium text-slate-400">${pct(n.progress)}%</span>` : ''}
        </div>
        ${inner.length ? `<div class="mt-1 mb-0.5 flex flex-wrap items-start gap-1">${inner.join('')}</div>` : ''}
      </div>`;
  }
}

function groupHTML(items) {
  const shown = items.slice(0, BUBBLE_MAX_PER_LEVEL);
  const more = items.length - shown.length;
  return shown.join('') + (more > 0 ? `<span class="self-center text-[11px] text-slate-400 px-1">+${more} more</span>` : '');
}

function bindBubbles(root) {
  root.querySelectorAll('[data-go]').forEach(el => el.addEventListener('click', e => {
    e.preventDefault(); e.stopPropagation(); go(el.dataset.go);
  }));
}

function findNode(tree, id) {
  if (tree.id === id) return tree;
  for (const c of tree.children) { const f = findNode(c, id); if (f) return f; }
  return null;
}

// Inline description: grows with its text and saves itself while you type.
function setupDescription(b) {
  const box = app.querySelector('#desc'), status = app.querySelector('#desc-status');
  const grow = () => { box.style.height = 'auto'; box.style.height = box.scrollHeight + 'px'; };
  let saved = b.notes, timer = null;
  const save = async () => {
    clearTimeout(timer);
    if (box.value === saved) return;
    const value = box.value;
    status.textContent = 'Saving…';
    try {
      await api('PATCH', `/api/blocks/${b.id}`, { notes: value });
      saved = value; b.notes = value;
      status.textContent = 'Saved ✓';
      animate(status, 'fade-in');
      setTimeout(() => { if (status.textContent === 'Saved ✓') status.textContent = ''; }, 1500);
    } catch (e) { status.textContent = ''; toast(e.message, 'error'); }
  };
  box.addEventListener('input', () => { grow(); clearTimeout(timer); timer = setTimeout(save, 700); });
  box.addEventListener('blur', save);
  grow();
}

// Stage buttons on a block's own page; stages its rules don't allow are disabled.
function stagePicker(b) {
  return `
    <div class="mt-3">
      <p class="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1.5">Phase</p>
      <div class="flex flex-wrap gap-1.5">
        ${curStages().map(s => {
          const on = s.key === b.status, ok = stageAllowed(b, s.key), st = STAGE_STYLE[s.key];
          return `<button data-stage="${s.key}" ${ok ? '' : 'disabled title="Its rules don\'t allow this right now"'}
            class="rounded-full border px-3 py-1 text-xs font-semibold transition ${on ? `bg-${st.color}-200 border-${st.color}-300 ${st.head}` : ok ? `bg-white border-${st.color}-200 ${st.head} hover:bg-${st.color}-50` : 'bg-white border-slate-200 text-slate-300 cursor-not-allowed'}">${esc(s.label)}</button>`;
        }).join('')}
      </div>
    </div>`;
}

// ---------------------------------------------------------------- board view
// Why a block is where it is: warnings, what it waits for, what it needs to move on, and automation.
function statusBadges(c) {
  const pill = (cls, html, title = '') => `<span class="inline-block rounded-2xl px-2 py-0.5 text-xs leading-snug ${cls}" ${title ? `title="${esc(title)}"` : ''}>${html}</span>`;
  const out = [];
  if (c.warning) out.push(pill('bg-blush-100 text-blush-700', `⚠️ Further along than its rules allow: waiting until ${esc(c.warning_reasons[0])}`));
  else if (c.locked) out.push(pill('bg-butter-100 text-butter-700', `${ICON.lock.replace('class="', 'class="inline -mt-0.5 mr-0.5 ')}Starts when ${esc(c.blocked_by.map(u => u.text).join(' and '))}`));
  else if (c.gate && !c.done) out.push(pill('bg-lavender-50 text-lavender-700', `→ ${esc(stageLabel(c.gate.stage))} when ${esc(c.gate.reasons.join(' and '))}`));
  if (c.automation.length) out.push(pill('bg-mint-50 text-mint-700', '⚡ Auto', c.automation.join('\n')));
  if (c.notes_count) out.push(pill('bg-slate-100 text-slate-500', `📝 ${c.notes_count}`, `${c.notes_count} note${c.notes_count > 1 ? 's' : ''}`));
  if (c.attachments_count) out.push(pill('bg-slate-100 text-slate-500', `${ico('paperclip')} ${c.attachments_count}`, `${c.attachments_count} link${c.attachments_count > 1 ? 's or files' : ' or file'}`));
  return out.join('');
}

// A to-do's optional counter (e.g. 37/200 videos): a bar with − / + to count as you go. The big one (a to-do's
// popup and page) also lets you type the number.
function trackHTML(c, big = false) {
  const t = c.track, btn = `shrink-0 ${big ? 'w-8 h-8 text-base' : 'w-5 h-5 text-xs'} rounded-md border border-lavender-200 text-lavender-700 hover:bg-lavender-50 font-bold leading-none`;
  const unit = t.unit ? ` <span class="font-sans">${esc(t.unit)}</span>` : '';
  const num = big
    ? `<input data-track-input type="number" min="0" max="${t.total}" value="${t.done}" aria-label="How many done" class="hud-num w-16 rounded-md border border-lavender-200 bg-white px-1.5 py-1 text-sm text-right">
       <span class="hud-num shrink-0 text-sm text-slate-500">/ ${t.total}${unit}</span>`
    : `<span data-track-text class="hud-num shrink-0 text-[11px] text-slate-500" title="${t.done} of ${t.total}${t.unit ? ' ' + esc(t.unit) : ''}"><span data-track-num>${t.done}/${t.total}</span>${unit}</span>`;
  return `<div class="mt-2 flex items-center gap-1.5" data-track="${c.id}" data-done="${t.done}" data-total="${t.total}">
    <div class="flex-1 min-w-[4rem]">${bar(t.done / t.total, big ? 'h-2.5' : 'h-1.5')}</div>
    ${num}
    <button type="button" data-track-add="-1" class="${btn}" aria-label="One less" title="One less">−</button>
    <button type="button" data-track-add="1" class="${btn}" aria-label="One more" title="One more">+</button>
  </div>`;
}

// For a to-do without a counter (popup and page): set one up right there.
function trackSetupHTML(c) {
  const field = 'rounded-md border border-lavender-200 bg-white px-2 py-1 text-sm';
  return `<details class="mt-2 text-sm" data-track-setup="${c.id}">
    <summary class="cursor-pointer text-xs font-semibold text-lavender-700 hover:underline">📊 Add a counter (e.g. 200 videos)</summary>
    <form class="mt-1.5 flex flex-wrap items-center gap-1.5 text-slate-500">
      <input name="total" type="number" min="1" max="1000000" required placeholder="Total" aria-label="Total" class="w-24 ${field}">
      <input name="unit" maxlength="24" placeholder="e.g. videos" aria-label="What's counted" class="w-32 ${field}">
      <button class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-semibold px-3 py-1.5">Add counter</button>
    </form>
  </details>`;
}

// − / + update the count on screen right away and save once you pause clicking; typing a number saves it.
function bindTrack(root) {
  root.querySelectorAll('[data-track]').forEach(box => {
    let pending = 0, timer = null;
    const input = box.querySelector('[data-track-input]');
    const show = n => {
      box.dataset.done = n;
      box.querySelector('[data-bar-fill]').style.width = `${Math.round(n / Number(box.dataset.total) * 100)}%`;
      const num = box.querySelector('[data-track-num]');
      if (num) { num.textContent = `${n}/${box.dataset.total}`; animate(num, 'tick'); }
      if (input) { input.value = n; animate(input, 'tick'); }
    };
    box.querySelectorAll('[data-track-add]').forEach(b => b.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      const done = Number(box.dataset.done);
      const next = Math.max(0, Math.min(Number(box.dataset.total), done + Number(b.dataset.trackAdd)));
      if (next === done) return;
      pending += next - done;
      show(next);
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const delta = pending; pending = 0;
        if (await attempt(() => api('PATCH', `/api/blocks/${box.dataset.track}`, { track_add: delta }))) refresh();
      }, 600);
    }));
    input?.addEventListener('click', e => e.stopPropagation());
    input?.addEventListener('change', async () => {
      clearTimeout(timer); pending = 0;
      const n = Math.max(0, Math.min(Number(box.dataset.total), Math.round(Number(input.value) || 0)));
      show(n);
      if (await attempt(() => api('PATCH', `/api/blocks/${box.dataset.track}`, { track_done: n }))) refresh();
    });
  });
  root.querySelectorAll('[data-track-setup]').forEach(d => d.querySelector('form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target, total = Math.round(Number(f.total.value) || 0);
    if (total < 1) return f.total.focus();
    if (await attempt(() => api('PATCH', `/api/blocks/${d.dataset.trackSetup}`, { track_total: total, track_done: 0, track_unit: f.unit.value.trim() }))) refresh();
  }));
}

function cardHTML(c, node) {
  const done = c.done;
  const st = STAGE_STYLE[c.status];
  const inside = node?.children?.length ? bubblesHTML(node.children) : '';
  return `
    <article draggable="true" data-id="${c.id}" class="card group ${isLiveStage(c.status) && !done ? 'is-live' : ''} rounded-2xl bg-white border ${c.warning ? 'border-blush-300' : st.card} p-3 shadow-sm hover:shadow-md hover:-translate-y-px transition cursor-pointer select-none">
      <div class="flex items-start justify-between gap-2">
        <h3 class="font-bold text-[15px] leading-snug break-words ${done ? 'text-slate-400 line-through decoration-mint-300' : 'text-slate-800'}">${esc(c.title)}</h3>
        <button data-edit="${c.id}" class="shrink-0 -mr-1 -mt-1 p-1 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-${st.color}-50" title="Edit, set what it waits for, duplicate…">${ICON.dots}</button>
      </div>
      ${c.notes ? `<p class="mt-1 text-xs text-slate-500 line-clamp-2 whitespace-pre-line break-words">${esc(c.notes)}</p>` : ''}
      ${c.track && !c.children_total ? trackHTML(c) : ''}
      ${c.children_total ? `
        <div class="mt-2 flex items-center gap-2">
          <div class="flex-1">${bar(c.progress, 'h-1.5', `c${c.id}`)}</div>
          <span class="text-xs text-slate-500 inline-flex items-center gap-1" title="Blocks nested inside">${ICON.nest}${c.children_done}/${c.children_total}</span>
        </div>` : ''}
      ${inside ? `<div class="mt-2.5 flex flex-wrap items-start gap-1">${inside}</div>` : ''}
      ${(() => { const badges = priorityBadge(c) + statusBadges(c) + dueBadge(c.due_date, done, c.due_time) + repeatBadge(c) + estimateBadge(c) + tagChips(c); return badges ? `<div class="mt-2 flex flex-wrap items-center gap-1.5">${badges}</div>` : ''; })()}
    </article>`;
}

// The board: the project's phases are shared columns. A to-do with nothing inside is a card in its
// phase's column. A to-do with to-dos inside is a bubble: an elastic outline around them whose name
// sits in the column of its slowest to-do and which stretches to its farthest one. Bubbles nest and
// hug each other line by line, so they reshape as you drag to-dos between columns.
// Columns fold into slim rails (remembered per phase). Phones: stage tabs, one column at a time.
const BAND_COLORS = ['lavender', 'baby', 'peach', 'lilac', 'mint', 'butter', 'blush'];
const BUBBLE_GAP = 7;   // px between nested outlines

function columnOpen(key, count, boardEmpty) {
  const pref = state.cols[key];
  if (pref) return pref === 'open';
  return boardEmpty || (count > 0 && key !== lastKey());   // a fresh board shows every phase
}

function renderBoard(view, data, opts = {}) {
  const cards = data.cards || {};
  const isLeaf = n => !n.children.length;
  const leaves = Object.values(cards).filter(c => !c.children_total);
  const count = Object.fromEntries(data.stages.map(s => [s.key, leaves.filter(c => c.status === s.key).length]));
  const open = Object.fromEntries(data.stages.map(s => [s.key, columnOpen(s.key, count[s.key], !leaves.length)]));
  if (!Object.values(open).some(Boolean)) open[firstKey()] = true;   // never fold everything away

  const mobile = matchMedia('(max-width: 767px)').matches;
  const active = count[state.mobileStage] !== undefined && state.mobileStage
    || (data.stages.find(s => s.key !== lastKey() && count[s.key]) || data.stages[0]).key;
  const cols = mobile ? data.stages.filter(s => s.key === active) : data.stages;
  const isOpen = key => mobile || open[key];
  const colOf = key => mobile ? 0 : Math.max(0, cols.findIndex(s => s.key === key));
  const shown = c => !mobile || c.status === active;

  // ---- lay out lines: each bubble gets a line with its name and its own cards, then its inner bubbles
  const lines = [];      // { owner, depth, items: [[html…] per column], span: [L, R] }
  const bubbles = [];    // { id, depth, color, lines: [line index…] }
  const newLine = (owner, depth) => ({ owner, depth, items: cols.map(() => []), used: new Set() });

  const emit = (node, chain, depth) => {
    const c = cards[node.id];
    if (!c) return;
    const color = BAND_COLORS[depth % BAND_COLORS.length];
    const me = { id: node.id, depth, color, lines: [] };
    bubbles.push(me);
    const myChain = [...chain, me];
    const collapsed = state.collapsed.has(node.id);
    const line = newLine(node.id, depth);
    line.tag = true;
    const tagCol = colOf(c.status);
    line.items[tagCol].push(isOpen(cols[tagCol].key) ? bubbleTag(c, color, collapsed, node)
      : `<a href="#/b/${c.id}" data-popup="${c.id}" class="bubble-dot block mx-auto w-3 h-3 rounded-full bg-${color}-300 ring-2 ring-white" title="${esc(c.title)}"></a>`);
    line.used.add(tagCol);
    line.span = [Math.min(...line.used), Math.max(...line.used)];
    lines.push(line);
    myChain.forEach(b => b.lines.push(lines.length - 1));
    // Its own cards get their own line under the name, so they start level in every phase.
    const own = collapsed ? [] : node.children.filter(isLeaf).map(n => cards[n.id]).filter(k => k && shown(k));
    if (own.length) {
      const row = newLine(node.id, depth);
      own.forEach(k => { row.items[colOf(k.status)].push(cardHTML(k, null)); row.used.add(colOf(k.status)); });
      row.span = [Math.min(...row.used), Math.max(...row.used)];
      lines.push(row);
      myChain.forEach(b => b.lines.push(lines.length - 1));
    }
    if (!collapsed) {
      bubblesIn(node).forEach(n => emit(n, myChain, depth + 1));
      pushAddLine(node.id, c.title, depth, Math.min(...me.lines.map(r => lines[r].span[0])), Math.max(...me.lines.map(r => lines[r].span[1])), myChain);
    }
  };
  // The last line of every level: "+ Add to-do to …", in the first open column of that level's box.
  const pushAddLine = (owner, title, depth, left, right, chain) => {
    let col = left;
    while (col <= right && !isOpen(cols[col].key)) col++;
    if (col > right) return;
    const line = newLine(owner, depth);
    line.add = true;
    const status = cols[col].key === lastKey() ? firstKey() : cols[col].key;
    line.items[col].push(`<button data-add-cell="${owner}" data-status="${status}" class="add-row w-full text-left truncate rounded-md border border-dashed border-lavender-200 px-2 py-1 text-xs font-semibold text-lavender-700 hover:border-lavender-300 hover:bg-lavender-50" title="Add a to-do to “${esc(title)}”">+ Add to-do to ${esc(title)}</button>`);
    line.span = [col, col];
    lines.push(line);
    chain.forEach(b => b.lines.push(lines.length - 1));
  };

  const rootLeaves = data.tree.children.filter(isLeaf).map(n => cards[n.id]).filter(k => k && shown(k));
  if (rootLeaves.length || !data.tree.children.length) {
    const line = newLine(data.block.id, -1);
    rootLeaves.forEach(k => line.items[colOf(k.status)].push(cardHTML(k, null)));
    lines.push(line);
  }
  bubblesIn(data.tree).forEach(n => emit(n, [], 0));
  pushAddLine(data.block.id, data.block.title, -1, 0, cols.length - 1, []);

  // ---- markup: sticky phase headers, then one grid row per line
  const top = opts.popup ? 'top-0' : 'top-14';
  const header = cols.map((s, i) => {
    const st = STAGE_STYLE[s.key];
    return isOpen(s.key) ? `
      <div data-colhead="${i}" class="group/head sticky ${top} z-20 flex items-center justify-between gap-1 px-3 py-2 bg-white/95 backdrop-blur border-b-2 border-${st.color}-200 ${i ? 'border-l border-l-slate-100' : 'rounded-tl-3xl'} ${i === cols.length - 1 ? 'rounded-tr-3xl' : ''}">
        <span class="flex items-center gap-2 min-w-0 text-sm font-bold ${st.head}">
          <button data-phase-color="${s.key}" class="w-2.5 h-2.5 shrink-0 rounded-full ${st.dot} ${isLiveStage(s.key) ? 'dot-live' : ''} hover:ring-2 hover:ring-${st.color}-200" title="Change color" aria-label="Change the color of ${esc(s.label)}"></button>
          <span data-phase-rename="${s.key}" role="button" tabindex="0" class="truncate cursor-text rounded px-0.5 -mx-0.5 hover:bg-${st.color}-50" title="Click to rename">${esc(s.label)}</span>
        </span>
        <span class="flex items-center gap-1 shrink-0">
          <span class="flex items-center md:opacity-0 md:group-hover/head:opacity-100 focus-within:opacity-100 transition-opacity">
            <button data-phase-add="${s.key}" class="p-1 rounded-lg text-xs font-bold ${st.head} opacity-70 hover:opacity-100 hover:bg-${st.color}-50" title="Add a phase after “${esc(s.label)}”" aria-label="Add a phase after ${esc(s.label)}">＋</button>
            <button data-phase-del="${s.key}" class="p-1 rounded-lg text-xs text-slate-400 hover:text-blush-700 hover:bg-blush-50 disabled:opacity-30 disabled:pointer-events-none" ${data.stages.length <= 2 ? 'disabled' : ''} title="Remove “${esc(s.label)}”" aria-label="Remove ${esc(s.label)}">✕</button>
          </span>
          <span class="text-xs font-semibold ${st.head} bg-${st.color}-50 rounded-full px-2 py-0.5">${count[s.key]}</span>
          ${mobile ? '' : `<button data-fold="${s.key}" class="p-1 rounded-lg ${st.head} opacity-60 hover:opacity-100 hover:bg-${st.color}-50" title="Fold into a slim rail" aria-label="Fold ${esc(s.label)}">${ICON.fold}</button>`}
        </span>
      </div>` : `
      <button data-colhead="${i}" data-unfold="${s.key}" title="Show “${esc(s.label)}” (${count[s.key]})" class="sticky ${top} z-20 flex flex-col items-center gap-1 py-2 bg-white/95 backdrop-blur border-b-2 border-${st.color}-200 border-l border-l-slate-100 ${i === cols.length - 1 ? 'rounded-tr-3xl' : ''}">
        <span class="w-2.5 h-2.5 rounded-full ${st.dot}"></span>
        <span class="text-[11px] font-semibold ${st.head}">${s.key === lastKey() && count[s.key] ? '✓' : ''}${count[s.key]}</span>
      </button>`;
  }).join('');

  const body = lines.map((line, r) => cols.map((s, i) => {
    const pad = (line.depth + 1) * BUBBLE_GAP + 6;
    const html = line.items[i].join('');
    return `<div class="cell group relative z-[1] ${line.add || line.tag ? '' : 'min-h-[2.75rem]'} space-y-1.5 ${i ? 'border-l border-l-slate-100/70' : ''}" data-row="${r}" data-col="${s.key}" data-parent="${line.owner}" data-line="${line.tag ? 'tag' : line.add ? 'add' : 'cards'}"
        style="padding:${line.add ? `2px ${isOpen(s.key) ? pad : 2}px ${pad - 2}px` : `${pad - 2}px ${isOpen(s.key) ? pad : 2}px`}">
      ${isOpen(s.key) ? html : foldedCell(line.items[i])}
    </div>`;
  }).join('')).join('');

  view.innerHTML = `
    ${mobile ? `<div class="flex flex-wrap gap-1.5 mb-3" role="tablist">
      ${data.stages.map(s => {
        const on = s.key === active, st = STAGE_STYLE[s.key];
        return `<button data-tab="${s.key}" role="tab" aria-selected="${on}" class="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition ${on ? `bg-${st.color}-100 border-${st.color}-300 ${st.head}` : `bg-white/70 border-${st.color}-200 ${st.head}`}">
          <span class="w-2 h-2 rounded-full ${st.dot}"></span>${esc(s.label)}<span class="opacity-70">${count[s.key]}</span></button>`;
      }).join('')}
    </div>` : ''}
    <div class="nb relative grid rounded-3xl border border-lavender-100 bg-white/70 shadow-sm" style="grid-template-columns:${widths(cols, isOpen)}">
      <svg class="nb-outlines absolute inset-0 w-full h-full pointer-events-none z-0" aria-hidden="true"></svg>
      ${header}
      ${body}
      <div class="col-span-full h-3"></div>
    </div>
  `;
  if (!opts.popup && localGet('nestwork.boardHint') !== 'off') {
    view.insertAdjacentHTML('afterbegin', `
      <div data-hint class="mb-2 flex items-start gap-3 rounded-md border border-lavender-200 bg-lavender-50/70 px-3 py-2 text-xs text-slate-600">
        <span class="flex-1"><b class="text-lavender-700">Board tips</b> · Add with <b>+ Add to-do</b> at the bottom of any level, <b>+ New to-do</b>, or <kbd class="hud-kbd">N</kbd> · <span class="hidden md:inline">Drag cards between phases · </span>Click a card or a box’s name to open it · Click a phase name to rename it</span>
        <button data-hint-close class="shrink-0 text-slate-400 hover:text-slate-700" aria-label="Hide these tips">✕</button>
      </div>`);
    view.querySelector('[data-hint-close]').onclick = () => { localSet('nestwork.boardHint', 'off'); const h = view.querySelector('[data-hint]'); animateOut([h], 'poof', 220).then(() => h.remove()); };
  }

  // Outlines are drawn after layout (and again whenever the board changes size).
  const nb = view.querySelector('.nb');
  const draw = () => drawBubbles(nb, bubbles, lines);
  requestAnimationFrame(draw);
  view._ro?.disconnect();
  view._ro = new ResizeObserver(draw);
  view._ro.observe(nb);

  const remember = (key, value) => {
    state.cols[key] = value; localSet('nestwork.cols', JSON.stringify(state.cols));
    renderBoard(view, data, opts);
  };
  view.querySelectorAll('[data-fold]').forEach(b => b.onclick = () => remember(b.dataset.fold, 'closed'));
  view.querySelectorAll('[data-unfold]').forEach(b => b.onclick = () => remember(b.dataset.unfold, 'open'));
  view.querySelectorAll('[data-tab]').forEach(t => t.onclick = () => {
    state.mobileStage = t.dataset.tab;
    renderBoard(view, data, opts);
    stagger(view.querySelectorAll('.card'));
  });
  view.querySelectorAll('[data-toggle-band]').forEach(b => b.onclick = () => {
    const id = Number(b.dataset.toggleBand);
    state.collapsed.has(id) ? state.collapsed.delete(id) : state.collapsed.add(id);
    localSet('nestwork.collapsed', JSON.stringify([...state.collapsed]));
    renderBoard(view, data, opts);
  });
  view.querySelectorAll('[data-add-cell]').forEach(b => b.onclick = () => {
    const pid = Number(b.dataset.addCell);
    newTodoModal({ parentId: pid, parentTitle: (pid === data.block.id ? data.block : cards[pid])?.title, status: b.dataset.status });
  });
  // Reading a to-do or bubble opens it in a popup over the board (ctrl/⌘-click a name opens it as a page).
  view.querySelectorAll('.card').forEach(card => {
    card.onclick = e => { if (!e.target.closest('[data-edit], [data-track]')) openBlockPopup(Number(card.dataset.id)); };
  });
  view.querySelectorAll('[data-popup]').forEach(a => a.onclick = e => {
    if (e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    openBlockPopup(Number(a.dataset.popup));
  });
  view.querySelectorAll('[data-edit]').forEach(btn => btn.onclick = e => { e.stopPropagation(); editDrawer(Number(btn.dataset.edit)); });
  bindPhaseHeaders(view, data);
  setupDrag(view, data);
  bindTrack(view);
  if (opts.popup) return;
  animateCards(view, leaves, motion.entering);
  animateBars(view, motion.entering);
}

// ---- Editing phases right on the board's column headers: rename, recolor, add after, remove.
// Everything goes through the same PUT /stages as the Phases dialog (stagesModal).
const phaseRows = stages => stages.map(s => ({ key: s.key, label: s.label, color: s.color, role: ['active', 'review'].includes(s.role) ? s.role : null }));

async function savePhases(blockId, rows, { redraw = true } = {}) {
  const res = await attempt(() => api('PUT', `/api/blocks/${blockId}/stages`, { stages: rows }));
  if (res) { registerStages(res.stages); notify(res); if (redraw) await refresh(); }
  return res;
}

// Turn a header label into a text field; Enter or leaving it saves, Esc cancels.
function renamePhase(label, blockId, stages, key) {
  if (label.querySelector('input')) return;
  const stage = stages.find(s => s.key === key);
  const input = document.createElement('input');
  input.value = stage.label;
  input.maxLength = 40;
  input.setAttribute('aria-label', 'Phase name');
  input.className = 'w-full min-w-[5rem] rounded-md border border-lavender-300 bg-white px-1.5 py-0.5 text-sm font-bold text-slate-800 focus:outline-none focus:ring-2 focus:ring-lavender-300';
  label.replaceChildren(input);
  label.classList.remove('truncate');
  input.focus();
  input.select();
  const restore = () => { label.textContent = stage.label; label.classList.add('truncate'); };
  let done = false;
  const finish = async save => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    if (!save || !name || name === stage.label) return restore();
    const rows = phaseRows(stages);
    rows.find(r => r.key === key).label = name;
    if (!await savePhases(blockId, rows)) restore();
  };
  input.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
  };
  input.onblur = () => finish(true);
  input.onclick = e => e.stopPropagation();
}

function bindPhaseHeaders(view, data) {
  const id = data.block.id;
  view.querySelectorAll('[data-phase-rename]').forEach(el => {
    const start = () => renamePhase(el, id, data.stages, el.dataset.phaseRename);
    el.onclick = start;
    el.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); start(); } };
  });
  view.querySelectorAll('[data-phase-color]').forEach(b => b.onclick = () => {
    const rows = phaseRows(data.stages), r = rows.find(r => r.key === b.dataset.phaseColor);
    r.color = PALETTE[(PALETTE.indexOf(r.color) + 1) % PALETTE.length];
    savePhases(id, rows);
  });
  view.querySelectorAll('[data-phase-add]').forEach(b => b.onclick = async () => {
    if (data.stages.length >= 10) return toast('A project can have up to 10 phases.', 'error');
    const rows = phaseRows(data.stages), i = rows.findIndex(r => r.key === b.dataset.phaseAdd);
    const used = new Set(rows.map(r => r.color));
    rows.splice(i + 1, 0, { label: 'New phase', color: PALETTE.find(c => !used.has(c)) || PALETTE[(i + 1) % PALETTE.length], role: null });
    const before = new Set(data.stages.map(s => s.key));
    const res = await savePhases(id, rows, { redraw: false });
    if (!res) return;
    const added = res.stages.find(s => !before.has(s.key));
    // Show the new (empty) column and put its name straight into edit mode.
    state.cols[added.key] = 'open';
    localSet('nestwork.cols', JSON.stringify(state.cols));
    state.mobileStage = added.key;
    await refresh();
    const label = (popup.root?.isConnected && popup.root.querySelector(`[data-phase-rename="${added.key}"]`)) || app.querySelector(`[data-phase-rename="${added.key}"]`);
    label?.click();
  });
  view.querySelectorAll('[data-phase-del]').forEach(b => b.onclick = () => {
    const key = b.dataset.phaseDel, i = data.stages.findIndex(s => s.key === key), stage = data.stages[i];
    if (data.stages.length <= 2) return toast('A project needs at least two phases.', 'error');
    const n = Object.values(data.cards || {}).filter(c => c.status === key).length;
    const dest = data.stages[i - 1] || data.stages[i + 1];
    const msg = `Remove the phase “${stage.label}”?`
      + (n ? ` The ${n} item${n > 1 ? 's' : ''} in it here (and anywhere else in the project) move to “${dest.label}”.` : '')
      + (i === 0 ? ` “${data.stages[1].label}” becomes the first phase.` : i === data.stages.length - 1 ? ` “${data.stages[i - 1].label}” becomes the done phase.` : '');
    if (confirm(msg)) savePhases(id, phaseRows(data.stages).filter(r => r.key !== key));
  });
}

function widths(cols, isOpen) {
  return cols.map(s => isOpen(s.key) ? 'minmax(9rem, 1fr)' : '2.75rem').join(' ');
}

// A folded column shows each bubble name as a small dot and a count of the cards hidden there.
function foldedCell(items) {
  const dots = items.filter(h => h.includes('bubble-dot'));
  const n = items.filter(h => !h.includes('bubble-dot') && !h.includes('add-row')).length;
  return dots.join('')
    + (n ? `<span class="mx-auto block w-5 h-5 rounded-full bg-slate-100 text-[10px] font-bold text-slate-500 text-center leading-5">${n}</span>` : '');
}

// A bubble's name tag: sits in the column of its slowest to-do.
function bubbleTag(c, color, collapsed, node) {
  const counts = collapsed ? state.data.stages.map(s => {
    const n = descendantsOf(node).filter(d => !d.children.length && d.status === s.key).length;
    return n ? `<span class="rounded-full px-1.5 text-[10px] font-semibold ${STAGE_STYLE[s.key].pill}">${n}</span>` : '';
  }).join('') : '';
  return `
    <div class="bubble-tag flex flex-wrap items-center gap-1 min-w-0" data-band="${c.id}">
      <button data-toggle-band="${c.id}" class="w-4 h-4 shrink-0 flex items-center justify-center rounded text-slate-400 hover:bg-white/70" aria-label="${collapsed ? 'Unfold' : 'Fold'} “${esc(c.title)}”">
        <span class="transition ${collapsed ? '' : 'rotate-90'}">${ICON.caret}</span></button>
      <a href="#/b/${c.id}" data-popup="${c.id}" class="bubble-name min-w-0 truncate text-sm font-extrabold text-${color}-700 hover:underline" title="${esc(c.title)}: ${pct(c.progress)}% done">${esc(c.title)}</a>
      ${c.locked ? `<span class="shrink-0 text-butter-700" title="Starts when ${esc(c.blocked_by.map(u => u.text).join(' and '))}">${ICON.lock}</span>` : ''}
      <span class="shrink-0 text-[10px] font-semibold text-slate-400">${pct(c.progress)}%</span>
      ${dueBadge(c.due_date, c.done, c.due_time)}
      ${counts}
      <button data-add-cell="${c.id}" data-status="${childPhase(c.status)}" class="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold text-slate-500 hover:text-lavender-700 hover:bg-white/70" title="Add a to-do inside “${esc(c.title)}”">+ Inside</button>
      <button data-edit="${c.id}" class="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold text-slate-500 hover:text-lavender-700 hover:bg-white/70" title="Edit “${esc(c.title)}”: details, rules, duplicate…">Edit</button>
    </div>`;
}

// Draw every bubble as one rounded outline hugging its lines: on each line it spans from the first to
// the last column used there, and deeper bubbles sit a little further inside their parents.
function drawBubbles(nb, bubbles, lines) {
  const svg = nb.querySelector('.nb-outlines');
  if (!svg) return;
  const box = nb.getBoundingClientRect();
  // Measured while the page may still be zooming in (a CSS transform), so convert the on-screen
  // rectangles back to the board's own, unscaled coordinates; otherwise outlines end up shrunk and shifted.
  const k = box.width / nb.offsetWidth || 1;
  const local = el => {
    const r = el.getBoundingClientRect();
    return { left: (r.left - box.left) / k, right: (r.right - box.left) / k, top: (r.top - box.top) / k, bottom: (r.bottom - box.top) / k };
  };
  const heads = [...nb.querySelectorAll('[data-colhead]')].map(local);
  if (!heads.length) return;
  const rowBox = lines.map((_, r) => { const c = nb.querySelector(`.cell[data-row="${r}"]`); return c && local(c); });
  const paths = bubbles.map(b => {
    const inset = 3 + b.depth * BUBBLE_GAP;
    // A box: as wide as every phase used anywhere inside it, on every line, so it reads as one container.
    const left = Math.min(...b.lines.map(r => lines[r].span[0])), right = Math.max(...b.lines.map(r => lines[r].span[1]));
    const segs = b.lines.map((r, j) => {
      const rb = rowBox[r];
      if (!rb) return null;
      let x1 = heads[left].left + inset, x2 = heads[right].right - inset;
      if (x2 - x1 < 8) { const mid = (x1 + x2) / 2; x1 = mid - 4; x2 = mid + 4; }
      return { x1, x2, y1: rb.top + (j === 0 ? inset : 0), y2: rb.bottom - (j === b.lines.length - 1 ? inset : 0) };
    }).filter(Boolean);
    if (!segs.length) return '';
    const pts = [[segs[0].x1, segs[0].y1]];
    segs.forEach((s, i) => { if (i) pts.push([segs[i - 1].x1, s.y1], [s.x1, s.y1]); pts.push([s.x1, s.y2]); });
    for (let i = segs.length - 1; i >= 0; i--) {
      const s = segs[i];
      pts.push([s.x2, s.y2], [s.x2, s.y1]);
      if (i) pts.push([segs[i - 1].x2, s.y1]);
    }
    const c = b.color;
    return `<path d="${roundedPath(pts, 5)}" fill="rgb(var(--c-${c}-100) / 0.28)" stroke="rgb(var(--c-${c}-300))" stroke-width="1.5" style="filter:drop-shadow(0 0 calc(var(--glow) * 4px) rgb(var(--c-${c}-300) / .7))"/>`;
  });
  svg.innerHTML = paths.join('');
}

// A closed path through axis-aligned points with rounded corners (radius clamped to each edge).
function roundedPath(points, radius) {
  const same = (a, b) => Math.abs(a[0] - b[0]) < 0.5 && Math.abs(a[1] - b[1]) < 0.5;
  let pts = points.filter((p, i) => i === 0 || !same(p, points[i - 1]));   // drop repeats…
  if (pts.length > 1 && same(pts[0], pts[pts.length - 1])) pts.pop();
  for (let changed = true; changed && pts.length > 3;) {                  // …then points mid-way along an edge
    changed = false;
    for (let i = 0; i < pts.length; i++) {
      const prev = pts[(i - 1 + pts.length) % pts.length], p = pts[i], next = pts[(i + 1) % pts.length];
      if ((Math.abs(prev[0] - p[0]) < 0.5 && Math.abs(p[0] - next[0]) < 0.5) || (Math.abs(prev[1] - p[1]) < 0.5 && Math.abs(p[1] - next[1]) < 0.5)) {
        pts.splice(i, 1); changed = true; break;
      }
    }
  }
  if (pts.length < 3) return '';
  const n = pts.length;
  let d = '';
  for (let i = 0; i < n; i++) {
    const p = pts[(i - 1 + n) % n], v = pts[i], q = pts[(i + 1) % n];
    const lp = Math.hypot(v[0] - p[0], v[1] - p[1]), lq = Math.hypot(q[0] - v[0], q[1] - v[1]);
    const r = Math.min(radius, lp / 2, lq / 2);
    const a = [v[0] + (p[0] - v[0]) * r / lp, v[1] + (p[1] - v[1]) * r / lp];
    const b = [v[0] + (q[0] - v[0]) * r / lq, v[1] + (q[1] - v[1]) * r / lq];
    d += `${i ? 'L' : 'M'}${a[0].toFixed(1)},${a[1].toFixed(1)} Q${v[0].toFixed(1)},${v[1].toFixed(1)} ${b[0].toFixed(1)},${b[1].toFixed(1)} `;
  }
  return d + 'Z';
}

// Bubbles keep a steady order (the order they were made in), whatever phase they're in.
function bubblesIn(node) {
  return node.children.filter(n => n.children.length).sort((a, b) => a.id - b.id);
}

function descendantsOf(node) {
  return node.children.flatMap(c => [c, ...descendantsOf(c)]);
}

// Mirrors the server rule: can't move forward past what the rules allow, or back below where they put it.
function stageAllowed(c, key) {
  const order = curStages().map(s => s.key);
  const t = order.indexOf(key), cur = order.indexOf(c.status);
  if (t === cur) return true;
  if (t > cur && t > order.indexOf(c.allowed_max)) return false;
  return t >= order.indexOf(c.auto_min);
}

// Drag a to-do anywhere on the board: the column under the pointer is its new phase, and the row under it
// picks the block it goes into (a block's name, its to-dos or its "+ Add" line; the gaps around rows keep
// its own block). It lands at the height you dropped it (a glowing line shows where). Phases its rules
// don't allow fade in its own block; in another block the server checks the rules when you drop.
function setupDrag(view, data) {
  const nb = view.querySelector('.nb');
  if (!nb) return;
  let dragged = null, target = null;
  const cells = [...view.querySelectorAll('.cell')];
  const heads = [...view.querySelectorAll('[data-colhead]')];
  const colKeys = cells.filter(c => c.dataset.row === '0').map(c => c.dataset.col);
  const firstCol = cells.filter(c => c.dataset.col === colKeys[0]);   // one per row, top to bottom
  const mark = document.createElement('div');
  mark.className = 'drop-mark';
  const chip = document.createElement('div');
  chip.className = 'drop-into';
  const titleOf = id => (id === data.block.id ? data.block : data.cards[id])?.title || '';

  const keyAt = x => {
    const h = heads.find(el => { const r = el.getBoundingClientRect(); return x >= r.left && x < r.right; });
    return h ? colKeys[Number(h.dataset.colhead)] ?? null : null;
  };
  const parentAt = y => {
    const row = firstCol.find(c => { const r = c.getBoundingClientRect(); return y >= r.top && y < r.bottom; });
    return row ? Number(row.dataset.parent) : dragged.parent_id;
  };
  // Where its cards sit in that block and phase: its to-dos' row, else its name's row (nothing there yet).
  const cellFor = (parent, key) => {
    const mine = cells.filter(c => c.dataset.col === key && Number(c.dataset.parent) === parent);
    return mine.find(c => c.dataset.line === 'cards') || mine.find(c => c.dataset.line === 'tag') || mine[0] || null;
  };
  const clear = () => {
    target?.cell.classList.remove('col-drop');
    view.querySelectorAll('.col-hot').forEach(c => c.classList.remove('col-hot'));
    mark.remove();
    chip.remove();
    target = null;
  };

  view.querySelectorAll('.card').forEach(card => {
    card.addEventListener('dragstart', e => {
      dragged = data.cards[card.dataset.id];
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', card.dataset.id);
      requestAnimationFrame(() => card.classList.add('dragging'));
      cells.forEach(cell => {
        if (Number(cell.dataset.parent) === dragged.parent_id && !stageAllowed(dragged, cell.dataset.col)) cell.classList.add('col-deny');
      });
    });
    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      clear();
      cells.forEach(cell => cell.classList.remove('col-deny'));
      dragged = null;
    });
  });

  nb.addEventListener('dragover', e => {
    if (!dragged) return;
    const key = keyAt(e.clientX);
    const parent = key ? parentAt(e.clientY) : null;
    const home = parent === dragged.parent_id;
    const cell = key && (!home || stageAllowed(dragged, key)) ? cellFor(parent, key) : null;
    if (!cell) { clear(); return; }
    e.preventDefault();
    const others = [...cell.querySelectorAll('.card')].filter(c => Number(c.dataset.id) !== dragged.id);
    const before = others.find(c => e.clientY < c.getBoundingClientRect().top + c.getBoundingClientRect().height / 2) || null;
    if (target?.cell !== cell) {
      clear();
      cell.classList.add('col-drop');
      cells.forEach(c => { if (c.dataset.col === key) c.classList.add('col-hot'); });
      if (!home) { chip.textContent = `Into “${titleOf(parent)}”`; cell.append(chip); }
    }
    target = { cell, key, parent, before };
    // Floats over the cards (doesn't push them around): above the card it goes before, or under the last one.
    const last = others[others.length - 1];
    const y = before ? before.offsetTop - 5 : last ? last.offsetTop + last.offsetHeight + 2 : cell.clientHeight / 2 - 1;
    if (mark.parentElement !== cell) cell.append(mark);
    mark.style.top = `${Math.max(0, y)}px`;
  });
  nb.addEventListener('dragleave', e => { if (!nb.contains(e.relatedTarget)) clear(); });
  nb.addEventListener('drop', async e => {
    e.preventDefault();
    const t = target, d = dragged;
    clear();
    if (!t || !d) return;
    const before_id = t.before ? Number(t.before.dataset.id) : null;
    const r = await attempt(() => t.parent === d.parent_id
      ? api('POST', `/api/blocks/${d.id}/move`, { status: t.key, before_id })
      : api('POST', `/api/blocks/${d.id}/move-into`, { parent_id: t.parent, status: t.key, before_id }));
    if (r) refresh();
  });
}

// ---------------------------------------------------------------- outline view
function renderOutline(view, data) {
  const stageOptions = n => data.stages.map(s => `<option value="${s.key}" ${s.key === n.status ? 'selected' : ''} ${stageAllowed(n, s.key) ? '' : 'disabled'}>${esc(s.label)}</option>`).join('');
  const row = (n, depth) => {
    const open = !state.collapsed.has(n.id);
    const kids = n.children.length;
    return `
      <li>
        <div class="flex items-center gap-2 py-1.5 pr-2 rounded-lg hover:bg-slate-50" style="padding-left:${depth * 1.25 + 0.25}rem">
          <button data-toggle="${n.id}" class="w-5 h-5 flex items-center justify-center rounded text-slate-400 hover:bg-slate-200 ${kids ? '' : 'invisible'}">
            <span class="transition ${open ? 'rotate-90' : ''}">${ICON.caret}</span></button>
          <span class="w-2 h-2 rounded-full shrink-0 ${STAGE_STYLE[n.status].dot} ${isLiveStage(n.status) && !n.done ? 'dot-live' : ''}"></span>
          <a href="#/b/${n.id}" class="flex-1 min-w-0 truncate ${n.done ? 'text-slate-400 line-through' : 'text-slate-800'} hover:text-lavender-800">${esc(n.title)}</a>
          ${n.locked ? `<span class="hidden sm:inline-flex items-center gap-1 text-xs text-butter-700" title="Starts when ${esc(n.blocked_by.join(' and '))}">${ICON.lock}<span class="max-w-[10rem] truncate">${esc(n.blocked_by.join(', '))}</span></span>` : ''}
          ${dueBadge(n.due_date, n.done, n.due_time)}
          ${kids ? `<span class="hidden sm:block w-20">${bar(n.progress)}</span><span class="text-xs text-slate-500 w-9 text-right">${pct(n.progress)}%</span>` : ''}
          <select data-status="${n.id}" class="text-xs rounded-md border border-slate-200 bg-white py-1 pl-2 pr-6 max-w-[8.5rem]">${stageOptions(n)}</select>
        </div>
        ${kids && open ? `<ul data-kids="${n.id}">${n.children.map(c => row(c, depth + 1)).join('')}</ul>` : ''}
      </li>`;
  };
  const roots = data.tree.children;
  view.innerHTML = roots.length ? `
    <div class="rounded-3xl bg-white border border-lavender-100 p-2 shadow-sm">
      <div class="flex justify-end gap-2 px-2 pb-1 text-xs">
        <button data-all="open" class="text-slate-500 hover:text-lavender-700">Expand all</button>
        <button data-all="close" class="text-slate-500 hover:text-lavender-700">Collapse all</button>
      </div>
      <ul>${roots.map(n => row(n, 0)).join('')}</ul>
      <button data-oadd class="mt-1 ml-2 mb-1 rounded-md border border-dashed border-lavender-200 px-2.5 py-1 text-xs font-semibold text-lavender-700 hover:border-lavender-300 hover:bg-lavender-50">+ Add to-do</button>
    </div>` : `<div class="py-8 text-center"><p class="text-slate-500">Nothing nested here yet.</p><button data-oadd class="mt-3 rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-sm px-4 py-2">+ Add the first to-do</button></div>`;
  view.querySelector('[data-oadd]').onclick = () => newTodoModal();

  const save = () => localSet('nestwork.collapsed', JSON.stringify([...state.collapsed]));
  view.querySelectorAll('[data-toggle]').forEach(b => b.onclick = () => {
    const id = Number(b.dataset.toggle);
    const opening = state.collapsed.has(id);
    opening ? state.collapsed.delete(id) : state.collapsed.add(id);
    save(); renderOutline(view, data);
    if (opening) animate(view.querySelector(`[data-kids="${id}"]`), 'expand');
  });
  view.querySelectorAll('[data-all]').forEach(b => b.onclick = () => {
    const walk = n => { n.children.forEach(walk); if (n.children.length) b.dataset.all === 'open' ? state.collapsed.delete(n.id) : state.collapsed.add(n.id); };
    roots.forEach(walk); save(); renderOutline(view, data);
    animate(view.firstElementChild, 'page-fade');
  });
  if (motion.entering) stagger(view.querySelectorAll('li'), 'pop-in', 18, 300);
  view.querySelectorAll('[data-status]').forEach(s => s.onchange = async () => {
    await attempt(() => api('POST', `/api/blocks/${s.dataset.status}/move`, { status: s.value }));
    refresh();
  });
}

// ---------------------------------------------------------------- adding a to-do
// One dialog for every "add" on a project page: what, where (this level or inside any to-do below it)
// and which phase. "Add" keeps it open for the next one; "More options…" opens the full edit panel.
// Where a new to-do inside a block starts: in the block's phase, so adding a to-do list doesn't push the
// block back to the start (same as the server's default). Top level, review and done start at the beginning.
function childPhase(parentStatus, isRoot = false) {
  const keys = curStages().map(s => s.key);
  const review = curStages().findIndex(s => s.role === 'review');
  const i = keys.indexOf(parentStatus);
  return !isRoot && i >= 0 && i < (review < 0 ? keys.length - 1 : review) ? parentStatus : keys[0];
}

function newTodoModal({ parentId = null, parentTitle = '', status = null } = {}) {
  const data = state.data;
  if (!data) return;
  parentId ??= data.block.id;
  const places = [];
  const walk = (n, depth) => { places.push({ id: n.id, title: n.title, depth, phase: childPhase(n.status, n.id === data.block.id && data.is_root) }); n.children.forEach(c => walk(c, depth + 1)); };
  walk(data.tree, 0);
  if (!places.some(p => p.id === parentId)) places.unshift({ id: parentId, title: parentTitle || 'This to-do', depth: 0, phase: childPhase(data.cards?.[parentId]?.status) });
  const phases = data.stages.slice(0, -1);   // the last phase means done
  const phaseFor = id => places.find(p => p.id === id)?.phase;
  const phase = [status, phaseFor(parentId)].find(k => phases.some(s => s.key === k)) || phases[0].key;
  const field = 'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 focus:outline-none';
  openModal(`
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-semibold">New to-do</h2>
      <button data-close class="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100" aria-label="Close">✕</button>
    </div>
    <label for="nt-title" class="block text-sm font-medium text-slate-700 mb-1">What</label>
    <input id="nt-title" maxlength="200" autocomplete="off" placeholder="e.g. Idle animation" class="${field}">
    <div class="mt-4 grid sm:grid-cols-[1fr,11rem] gap-3">
      <label class="block"><span class="block text-sm font-medium text-slate-700 mb-1">Where</span>
        <select id="nt-where" class="${field}">${places.map(p => `<option value="${p.id}" ${p.id === parentId ? 'selected' : ''}>${' '.repeat(p.depth)}${p.depth ? '↳ ' : ''}${esc(p.title)}${p.id === data.block.id ? ' (top level here)' : ''}</option>`).join('')}</select></label>
      <label class="block"><span class="block text-sm font-medium text-slate-700 mb-1">Phase</span>
        <select id="nt-phase" class="${field}">${phases.map(s => `<option value="${s.key}" ${s.key === phase ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}</select></label>
    </div>
    <p id="nt-added" class="mt-3 min-h-[1.25rem] text-sm text-mint-700"></p>
    <div class="flex flex-wrap items-center justify-between gap-2 mt-3 pt-4 border-t border-slate-200">
      <button id="nt-more" class="text-sm text-slate-500 hover:text-lavender-700" title="Description, due date, daily pomodoros, rules">More options…</button>
      <div class="flex gap-2">
        <button data-close class="rounded-lg px-4 py-2 text-slate-600 hover:bg-slate-100">Done</button>
        <button id="nt-add" class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white font-medium px-5 py-2">Add</button>
      </div>
    </div>`, root => {
    const $ = q => root.querySelector(q);
    const add = async () => {
      const title = $('#nt-title').value.trim();
      if (!title) return $('#nt-title').focus();
      const parent = Number($('#nt-where').value), status = $('#nt-phase').value;
      const r = await attempt(() => api('POST', '/api/blocks', { title, parent_id: parent, status }));
      if (!r) return;
      const where = places.find(p => p.id === parent);
      $('#nt-added').textContent = `✓ Added “${title}” to ${where ? `“${where.title}”` : 'this level'}. Type the next one, or Done.`;
      animate($('#nt-added'), 'fade-in');
      $('#nt-title').value = '';
      $('#nt-title').focus();
      await refresh();
    };
    $('#nt-add').onclick = add;
    // The phase follows the chosen place until you pick one yourself.
    let phaseTouched = !!status;
    $('#nt-phase').addEventListener('change', () => { phaseTouched = true; });
    $('#nt-where').addEventListener('change', () => {
      const k = phaseFor(Number($('#nt-where').value));
      if (!phaseTouched && phases.some(s => s.key === k)) $('#nt-phase').value = k;
    });
    $('#nt-title').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); add(); } });
    $('#nt-more').onclick = async () => {
      const title = $('#nt-title').value, where = places.find(p => p.id === Number($('#nt-where').value));
      const status = $('#nt-phase').value;
      closeModal();
      await editDrawer(null, false, { parent: { id: where.id, title: where.title }, status });
      const t = modalRoot.querySelector('#ed-title');
      if (t) t.value = title;
    };
    setTimeout(() => $('#nt-title').focus(), 50);
  }, { stack: openLayers().length > 0 });
}

// ---------------------------------------------------------------- tree view
// The whole nest as a tree drawn left to right: this block, then what's inside it, branch by branch.
// Click a name to open it, ▸ to fold a branch, + to add a to-do inside, "+ Add to-do" ends every level.
function renderTree(view, data) {
  const ghost = (id, title) => `<li class="tree-ghost"><button data-tadd="${id}" class="rounded-md border border-dashed border-lavender-200 px-2.5 py-1 text-xs font-semibold text-lavender-700 hover:border-lavender-300 hover:bg-lavender-50" title="Add a to-do to “${esc(title)}”">+ Add to-do</button></li>`;
  const branch = (n, isRoot = false) => {
    const kids = n.children, open = isRoot || !state.collapsed.has(n.id), st = STAGE_STYLE[n.status];
    const card = `
      <div class="tree-node group ${isRoot ? 'tree-root' : ''} flex items-center gap-2 rounded-md border ${n.locked ? 'border-dashed' : ''} border-${st.color}-200 bg-white px-2.5 py-1.5" data-tnode="${n.id}">
        ${!isRoot && kids.length ? `<button data-ttoggle="${n.id}" class="w-4 h-4 shrink-0 flex items-center justify-center text-slate-400 hover:text-slate-700" aria-label="${open ? 'Fold' : 'Unfold'} “${esc(n.title)}”" aria-expanded="${open}"><span class="transition ${open ? 'rotate-90' : ''}">${ICON.caret}</span></button>` : ''}
        <span class="w-2 h-2 shrink-0 ${st.dot} ${isLiveStage(n.status) && !n.done ? 'dot-live' : ''}" title="${esc(stageLabel(n.status))}"></span>
        ${isRoot ? `<span class="font-display font-bold text-sm text-slate-900 whitespace-nowrap">${esc(n.title)}</span>`
          : `<a href="#/b/${n.id}" data-popup="${n.id}" class="text-sm font-semibold whitespace-nowrap ${n.done ? 'text-slate-400 line-through' : 'text-slate-800'} hover:text-lavender-700" title="${esc(n.title)} · ${esc(stageLabel(n.status))}">${esc(n.title)}</a>`}
        ${n.locked ? `<span class="text-butter-700" title="Starts when ${esc(n.blocked_by.join(' and '))}">${ICON.lock}</span>` : ''}
        ${kids.length ? `<span class="hud-num text-[11px] text-slate-400">${pct(n.progress)}%</span>` : ''}
        ${!open ? `<span class="rounded px-1.5 text-[10px] font-semibold bg-${st.color}-100 ${st.head}">${descendantsOf(n).length} inside</span>` : ''}
        ${dueBadge(n.due_date, n.done, n.due_time)}
        ${isRoot ? '' : `<button data-tadd="${n.id}" class="tree-plus shrink-0 rounded px-1 text-sm leading-none text-slate-400 hover:text-lavender-700 hover:bg-lavender-50" title="Add a to-do inside “${esc(n.title)}”" aria-label="Add a to-do inside ${esc(n.title)}">+</button>`}
      </div>`;
    const inner = open ? `<ul data-tkids="${n.id}">${kids.map(c => `<li>${branch(c)}</li>`).join('')}${isRoot || kids.length ? ghost(n.id, n.title) : ''}</ul>` : '';
    return `<div class="tree-branch">${card}${kids.length || isRoot ? inner : ''}</div>`;
  };
  const root = { ...data.tree, title: data.block.title, status: data.block.status, done: data.block.done, progress: data.block.progress, due_date: null };
  view.innerHTML = `
    <div class="rounded-3xl bg-white/70 border border-lavender-100 shadow-sm">
      <div class="flex flex-wrap items-center justify-between gap-2 px-3 pt-2 text-xs">
        <span class="text-slate-400">Click a name to open it · ▸ folds a branch · + adds a to-do inside</span>
        <span class="flex gap-3"><button data-all="open" class="text-slate-500 hover:text-lavender-700">Expand all</button><button data-all="close" class="text-slate-500 hover:text-lavender-700">Collapse all</button></span>
      </div>
      <div class="tree overflow-x-auto scroll-thin p-4">${branch(root, true)}</div>
    </div>`;
  const save = () => localSet('nestwork.collapsed', JSON.stringify([...state.collapsed]));
  view.querySelectorAll('[data-ttoggle]').forEach(b => b.onclick = () => {
    const id = Number(b.dataset.ttoggle), opening = state.collapsed.has(id);
    opening ? state.collapsed.delete(id) : state.collapsed.add(id);
    save(); renderTree(view, data);
    if (opening) stagger(view.querySelectorAll(`[data-tkids="${id}"] > li > .tree-branch > .tree-node, [data-tkids="${id}"] > .tree-ghost`), 'pop-in', 30);
  });
  view.querySelectorAll('[data-all]').forEach(b => b.onclick = () => {
    const walk = n => { n.children.forEach(walk); if (n.children.length) b.dataset.all === 'open' ? state.collapsed.delete(n.id) : state.collapsed.add(n.id); };
    data.tree.children.forEach(walk); save(); renderTree(view, data);
    animate(view.firstElementChild, 'page-fade');
  });
  view.querySelectorAll('[data-tadd]').forEach(b => b.onclick = () => {
    const id = Number(b.dataset.tadd);
    newTodoModal({ parentId: id, parentTitle: id === data.block.id ? data.block.title : findNode(data.tree, id)?.title });
  });
  view.querySelectorAll('[data-popup]').forEach(a => a.onclick = e => {
    if (e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    openBlockPopup(Number(a.dataset.popup));
  });
  if (motion.entering) stagger(view.querySelectorAll('.tree-node, .tree-ghost'), 'pop-in', 22, 500);
}

// ---------------------------------------------------------------- "what can I do now" view
function renderNext(view, data) {
  const leaves = [];
  const walk = (n, path) => n.children.forEach(c => {
    if (c.children.length) walk(c, [...path, c.title]);
    else if (!c.done) leaves.push({ ...c, path: path.join(' › ') });
  });
  walk(data.tree, []);
  const groups = [
    // Furthest-along phases first, then what's waiting on something else.
    ...data.stages.slice(0, -1).reverse().map(s => [s.key, s.label, leaves.filter(l => !l.locked && l.status === s.key)]),
    [firstKey(), 'Blocked — waiting on something else', leaves.filter(l => l.locked)],
  ];
  const item = l => `
    <li class="flex items-center gap-3 py-2 px-3 rounded-lg hover:bg-slate-50">
      <span class="w-2 h-2 rounded-full shrink-0 ${STAGE_STYLE[l.status].dot}"></span>
      <div class="min-w-0 flex-1">
        <a href="#/b/${l.id}" class="font-medium text-slate-800 hover:text-lavender-800 break-words">${esc(l.title)}</a>
        ${l.path ? `<div class="text-xs text-slate-400 truncate">${esc(l.path)}</div>` : ''}
        ${l.locked ? `<div class="text-xs text-butter-700 flex items-center gap-1 mt-0.5">${ICON.lock}Starts when ${esc(l.blocked_by.join(' and '))}</div>` : ''}
      </div>
      ${dueBadge(l.due_date, false, l.due_time)}
      <span class="hidden sm:inline rounded-full px-2 py-0.5 text-xs ${STAGE_STYLE[l.status].pill}">${esc(stageLabel(l.status))}</span>
    </li>`;
  const nonEmpty = groups.filter(g => g[2].length);
  view.innerHTML = nonEmpty.length ? `
    <div class="grid gap-4 lg:grid-cols-2">
      ${nonEmpty.map(([key, title, list]) => `
        <section class="rounded-3xl bg-white border border-lavender-100 p-3 shadow-sm">
          <h2 class="flex items-center gap-2 px-3 pt-1 pb-2 text-sm font-semibold ${STAGE_STYLE[key].head}">
            <span class="w-2 h-2 rounded-full ${STAGE_STYLE[key].dot}"></span>${title}<span class="text-slate-400 font-normal">${list.length}</span></h2>
          <ul>${list.map(item).join('')}</ul>
        </section>`).join('')}
    </div>
    <p class="text-xs text-slate-400 mt-3">Lists the smallest tasks at every nesting level under “${esc(data.block.title)}”.</p>`
    : `<p class="text-slate-500 py-8 text-center">${data.tree.children.length ? '🎉 Everything here is done.' : 'Nothing nested here yet.'}</p>`;
  if (motion.entering) stagger(view.querySelectorAll('section, li'), 'pop-in', 25, 400);
}

// ---------------------------------------------------------------- filters & saved views
// A filter bar above a block's views: words (in the title or description), a tag, a priority, a due date
// range, and hiding finished to-dos. Matching to-dos stay as they are and the rest dim, so bubbles and
// columns keep their shape; a bubble stays lit when anything inside it matches. The filter is remembered
// per project, and named views (saved filters) work on any project. Both live in this browser.
const FILTER_EMPTY = { q: '', tag: '', prio: 0, due: '', hideDone: false };
const filterKey = rootId => `nestwork.filter.${rootId}`;
function getFilter(rootId) {
  try { return { ...FILTER_EMPTY, ...(JSON.parse(localGet(filterKey(rootId)) || '{}') || {}) }; } catch { return { ...FILTER_EMPTY }; }
}
function setFilter(rootId, f) { localSet(filterKey(rootId), JSON.stringify(f)); }
const filterActive = f => !!(f.q.trim() || f.tag || f.prio || f.due || f.hideDone);
function savedViews() { try { return JSON.parse(localGet('nestwork.views') || '[]') || []; } catch { return []; } }
function saveViews(v) { localSet('nestwork.views', JSON.stringify(v)); }

function cardMatches(c, f, today) {
  if (f.hideDone && c.done) return false;
  const q = f.q.trim().toLowerCase();
  if (q && !q.split(/\s+/).every(w => `${c.title} ${c.notes} ${(c.tags || []).map(t => '#' + t).join(' ')}`.toLowerCase().includes(w))) return false;
  if (f.tag && !(c.tags || []).some(t => t.toLowerCase() === f.tag.toLowerCase())) return false;
  if (f.prio && (c.priority || 0) < f.prio) return false;
  if (f.due) {
    const d = c.due_date, week = isoDay(new Date(new Date(today + 'T00:00').getTime() + 7 * 864e5));
    if (f.due === 'overdue' && !(d && d < today && !c.done)) return false;
    if (f.due === 'today' && d !== today) return false;
    if (f.due === 'week' && !(d && d >= today && d <= week)) return false;
    if (f.due === 'any' && !d) return false;
    if (f.due === 'none' && d) return false;
  }
  return true;
}

function filterBarHTML(data) {
  const rootId = data.root_id, f = getFilter(rootId);
  const tags = [...new Set(Object.values(data.cards).flatMap(c => c.tags || []))].sort((a, b) => a.localeCompare(b));
  const views = savedViews();
  const sel = (attr, options, value, label) => `<select ${attr} aria-label="${label}" class="rounded-lg border ${value ? 'border-lavender-300 bg-lavender-50 text-lavender-800' : 'border-slate-200 bg-white text-slate-600'} py-1 pl-2 pr-7 text-xs">${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(value) ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
  return `
    <div class="shrink-0 mb-2 flex flex-wrap items-center gap-1.5" data-filterbar>
      <label class="relative flex-1 min-w-[10rem] max-w-xs">
        <span class="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400">${ico('search')}</span>
        <input data-f="q" value="${esc(f.q)}" placeholder="Filter to-dos…" autocomplete="off" class="w-full rounded-lg border ${f.q ? 'border-lavender-300' : 'border-slate-200'} bg-white py-1 pl-7 pr-2 text-xs">
      </label>
      ${tags.length ? sel('data-f="tag"', [['', 'Any tag'], ...tags.map(t => [t, '#' + t])], f.tag, 'Tag') : ''}
      ${sel('data-f="prio"', [[0, 'Any priority'], [1, 'Low and up'], [2, 'Medium and up'], [3, 'High only']], f.prio, 'Priority')}
      ${sel('data-f="due"', [['', 'Any due date'], ['overdue', 'Overdue'], ['today', 'Due today'], ['week', 'Due in 7 days'], ['any', 'Has a due date'], ['none', 'No due date']], f.due, 'Due')}
      <label class="inline-flex items-center gap-1.5 rounded-lg border ${f.hideDone ? 'border-lavender-300 bg-lavender-50' : 'border-slate-200 bg-white'} px-2 py-1 text-xs text-slate-600 cursor-pointer"><input type="checkbox" data-f="hideDone" ${f.hideDone ? 'checked' : ''}>Hide done</label>
      ${sel('data-fview', [['', views.length ? 'Saved views…' : 'Save as a view…'], ...views.map((v, i) => [i, `★ ${v.name}`]), ...(filterActive(f) ? [['save', '+ Save this filter as a view…']] : []), ...(views.length ? [['manage', 'Delete a view…']] : [])], '', 'Saved views')}
      ${filterActive(f) ? '<button data-fclear class="rounded-lg px-2 py-1 text-xs text-slate-500 hover:text-lavender-700">Clear</button>' : ''}
      <span data-fcount class="text-[11px] text-slate-400"></span>
    </div>`;
}

// The longest chain lit up on the board: its cards get a glow and their place in the line (1, 2, 3…).
function applyChain(root, data) {
  const ids = state.showChain ? (data.forecast?.chain || []).map(c => c.id) : [];
  root.querySelectorAll('.chain-on').forEach(el => { el.classList.remove('chain-on'); el.querySelector('.chain-n')?.remove(); });
  ids.forEach((id, i) => root.querySelectorAll(`#view .card[data-id="${id}"], #view [data-tnode="${id}"]`).forEach(el => {
    el.classList.add('chain-on');
    el.insertAdjacentHTML('afterbegin', `<span class="chain-n" title="Step ${i + 1} of ${ids.length} in the longest chain">${i + 1}</span>`);
  }));
  document.querySelectorAll('[data-chain-toggle]').forEach(b => {
    b.textContent = state.showChain ? 'Hide on board' : 'Show on board';
    b.onclick = () => { state.showChain = !state.showChain; applyChain(root, data); };
  });
}

// Dim what doesn't match in the current view (board cards, bubble names, tree nodes, outline rows).
function applyFilter(root, data) {
  const view = root.querySelector('#view');
  if (!view) return;
  const f = getFilter(data.root_id), on = filterActive(f), cards = data.cards;
  const kids = {};
  Object.values(cards).forEach(c => (kids[c.parent_id] ||= []).push(c.id));
  const memo = {};
  const lit = id => memo[id] ??= cards[id] ? (cardMatches(cards[id], f, data.today) || (kids[id] || []).some(lit)) : true;
  const targets = [
    ...[...view.querySelectorAll('.card[data-id]')].map(el => [el, el.dataset.id]),
    ...[...view.querySelectorAll('[data-popup]')].map(el => [el, el.dataset.popup]),
    ...[...view.querySelectorAll('[data-tnode]')].map(el => [el, el.dataset.tnode]),
    ...[...view.querySelectorAll('select[data-status]')].map(el => [el.closest('div'), el.dataset.status]),
  ];
  targets.forEach(([el, id]) => el && el.classList.toggle('flt-dim', on && !lit(Number(id))));
  const leaves = Object.values(cards).filter(c => !c.children_total);
  const count = root.querySelector('[data-fcount]');
  if (count) count.textContent = on ? `${leaves.filter(c => cardMatches(c, f, data.today)).length} of ${leaves.length} to-dos match` : '';
}

function bindFilterBar(root, data) {
  const bar = root.querySelector('[data-filterbar]');
  if (!bar) return;
  const rootId = data.root_id;
  const update = patch => { setFilter(rootId, { ...getFilter(rootId), ...patch }); redraw(); };
  const redraw = () => {
    const focus = document.activeElement?.dataset?.f, caret = document.activeElement?.selectionStart;
    bar.outerHTML = filterBarHTML(data);
    bindFilterBar(root, data);
    applyFilter(root, data);
    if (focus === 'q') { const q = root.querySelector('[data-f="q"]'); q.focus(); q.setSelectionRange(caret, caret); }
  };
  let t = null;
  bar.querySelector('[data-f="q"]').addEventListener('input', e => { clearTimeout(t); t = setTimeout(() => update({ q: e.target.value }), 180); });
  bar.querySelector('[data-f="q"]').addEventListener('keydown', e => { if (e.key === 'Escape') { e.target.value = ''; update({ q: '' }); e.target.blur(); } });
  bar.querySelectorAll('select[data-f]').forEach(s => s.onchange = () => update({ [s.dataset.f]: s.dataset.f === 'prio' ? Number(s.value) : s.value }));
  bar.querySelector('[data-f="hideDone"]').onchange = e => update({ hideDone: e.target.checked });
  bar.querySelector('[data-fclear]')?.addEventListener('click', () => { setFilter(rootId, { ...FILTER_EMPTY }); redraw(); });
  bar.querySelector('[data-fview]').onchange = e => {
    const v = e.target.value, views = savedViews();
    if (v === 'save') {
      const name = prompt('Name this view (it works on every project):', getFilter(rootId).tag ? `#${getFilter(rootId).tag}` : '');
      if (name?.trim()) { const { q, tag, prio, due, hideDone } = getFilter(rootId); saveViews([...views.filter(x => x.name !== name.trim()), { name: name.trim(), filter: { q, tag, prio, due, hideDone } }]); toast(`Saved the view “${name.trim()}”.`, 'success'); }
    } else if (v === 'manage') {
      const name = prompt(`Delete which view? ${views.map(x => `“${x.name}”`).join(', ')}`);
      if (name && views.some(x => x.name === name.trim())) { saveViews(views.filter(x => x.name !== name.trim())); toast(`Deleted the view “${name.trim()}”.`, 'success'); }
    } else if (v !== '') setFilter(rootId, { ...FILTER_EMPTY, ...views[Number(v)].filter });
    redraw();
  };
  // Views redraw themselves (folding, toggles): keep the dimming in step.
  const view = root.querySelector('#view');
  if (view && !view._filterObserver) {
    let pending = false;
    view._filterObserver = new MutationObserver(() => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => { pending = false; view._filterObserver.disconnect(); applyFilter(root, data); applyChain(root, data); view._filterObserver.observe(view, { childList: true, subtree: true }); });
    });
    view._filterObserver.observe(view, { childList: true, subtree: true });
  }
}
