// Chat with the Nestwork agent (Claude Code run by the backend). Uses helpers from app.js.
// The panel sits on the right like the edit panel; when an edit/add panel opens, it slides to the
// left of it (CSS in index.html) instead of being covered or closed.

const chat = {
  panel: document.getElementById('chat'),
  log: document.getElementById('chat-log'),
  form: document.getElementById('chat-form'),
  input: document.getElementById('chat-input'),
  send: document.getElementById('chat-send'),
  status: document.getElementById('chat-status'),
  tray: document.getElementById('chat-tray'),
  fileInput: document.getElementById('chat-file'),
  files: [],         // attachments waiting to be sent: {file, url} (url: a preview for images)
  loaded: false,
  running: false,
  forceNew: false,
  agentName: 'Agent',
  lastId: null,      // newest chat message we've shown or announced
  firstId: null,     // oldest message in the log; older ones load when you scroll up
  hasMore: false,
  loadingOlder: false,
  latest: document.getElementById('chat-latest'),
};
const PAGE = 30;     // messages rendered per load: long chats stay light until you scroll back

// ---------------------------------------------------------------- tiny Markdown renderer
function md(src) {
  const code = [];
  src = src.replace(/```[\w-]*\n?([\s\S]*?)```/g, (_, c) => `\u0000${code.push(c) - 1}\u0000`);
  const inline = t => esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  let html = '', para = [], list = null, quote = [];
  const flushQuote = () => { if (quote.length) html += `<blockquote>${quote.map(inline).join('<br>')}</blockquote>`; quote = []; };
  const flushPara = () => { flushQuote(); if (para.length) html += `<p>${para.map(inline).join('<br>')}</p>`; para = []; };
  const flushList = () => { if (list) html += `<${list.tag}>${list.items.map(i => `<li>${inline(i)}</li>`).join('')}</${list.tag}>`; list = null; };
  for (const line of src.split('\n')) {
    let m;
    if ((m = line.match(/^\u0000(\d+)\u0000$/))) { flushPara(); flushList(); html += `<pre><code>${esc(code[m[1]])}</code></pre>`; }
    else if ((m = line.match(/^(#{1,3})\s+(.*)/))) { flushPara(); flushList(); html += `<h${m[1].length + 2}>${inline(m[2])}</h${m[1].length + 2}>`; }
    else if ((m = line.match(/^\s*[-*•]\s+(.*)/)) || (m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      const tag = /^\s*\d/.test(line) ? 'ol' : 'ul';
      flushPara();
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(m[1]);
    }
    else if ((m = line.match(/^>\s?(.*)/))) { if (para.length) { const q = quote; quote = []; flushPara(); quote = q; } flushList(); quote.push(m[1]); }
    else if (!line.trim()) { flushPara(); flushList(); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList();
  return html.replace(/\u0000(\d+)\u0000/g, (_, n) => `<code>${esc(code[n])}</code>`);
}

// ---------------------------------------------------------------- rendering
function sessionNote(meta) {
  if (!meta?.reason) return '';
  return `<div class="flex items-center gap-2 py-1 text-[11px] text-slate-400"><span class="h-px flex-1 bg-slate-200"></span>✦ New session: ${esc(meta.reason)}<span class="h-px flex-1 bg-slate-200"></span></div>`;
}

const isPicture = mime => (mime || '').startsWith('image/') && mime !== 'image/svg+xml';

// Files sent with a message: pictures as thumbnails, anything else as a chip. Each f: {name, mime, size, href}.
function sentFilesHTML(files) {
  if (!files?.length) return '';
  return `<div class="flex flex-wrap justify-end gap-1.5 max-w-[85%]">${files.map(f => isPicture(f.mime)
    ? `<a href="${esc(f.href)}" target="_blank" rel="noopener" data-sent-pic="${esc(f.name)}" class="block rounded-xl overflow-hidden border border-lavender-100 hover:shadow-md transition" title="${esc(f.name)}">
        <img src="${esc(f.href)}" alt="${esc(f.name)}" class="block h-28 max-w-[12rem] w-auto object-cover"></a>`
    : `<a href="${esc(f.href)}" target="_blank" rel="noopener" class="flex items-center gap-2 max-w-[14rem] rounded-xl border border-lavender-100 bg-white px-2 py-1.5 hover:bg-lavender-50">
        <span class="w-7 h-7 shrink-0 flex items-center justify-center rounded bg-lavender-50 text-lavender-700">${ico('paperclip')}</span>
        <span class="min-w-0"><span class="block truncate text-xs font-semibold text-slate-700">${esc(f.name)}</span>
        <span class="block text-[10px] text-slate-400">${fileSize(f.size || 0)}</span></span></a>`).join('')}</div>`;
}

// When a message was sent: `at` is the server's created_at (UTC) or a Date; false shows nothing (still typing).
function stamp(at = new Date()) {
  if (at === false) return '';
  const d = at instanceof Date ? at : new Date(`${at.replace(' ', 'T')}Z`);
  if (isNaN(d)) return '';
  const now = new Date(), time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  const day = d.toDateString() === now.toDateString() ? ''
    : d.toDateString() === yesterday.toDateString() ? 'Yesterday '
    : `${d.toLocaleDateString([], { month: 'short', day: 'numeric', ...(d.getFullYear() !== now.getFullYear() && { year: 'numeric' }) })}, `;
  return `<time class="text-[10px] text-slate-400" datetime="${d.toISOString()}" title="${esc(d.toLocaleString())}">${day}${time}</time>`;
}

function userBubble(text, meta, files, at) {
  files ??= meta?.files?.map(f => ({ ...f, href: `/api/chat/files/${encodeURIComponent(f.id)}` }));
  return `${meta?.mode === 'new' ? sessionNote(meta) : ''}
    <div class="msg flex flex-col items-end gap-1.5">${sentFilesHTML(files)}
      ${text ? `<div class="max-w-[85%] rounded-2xl rounded-br-md bg-lavender-100 text-slate-800 px-3.5 py-2 text-sm whitespace-pre-wrap break-words">${esc(text)}</div>` : ''}
      <div class="-mt-1 px-1">${stamp(at)}</div></div>`;
}

function toolChips(tools) {
  if (!tools?.length) return '';
  const seen = [...new Map(tools.map(t => [t.label, t])).values()];
  return `<div class="tools flex flex-wrap gap-1 mb-1.5">${seen.map(t => `<span class="rounded-full bg-mint-50 border border-mint-100 text-mint-700 px-2 py-0.5 text-[11px]">🔧 ${esc(t.label)}</span>`).join('')}</div>`;
}

function metaLine(m, at) {
  const parts = [m?.stopped && 'stopped', stamp(at)].filter(Boolean);
  return parts.length ? `<p class="mt-1 text-[10px] text-slate-400">${parts.join(' · ')}</p>` : '';
}

function reminderActions(meta) {
  const b = meta.block_id;
  const btn = (attr, label) => `<button ${attr} class="rounded-full border border-butter-200 bg-white/70 px-2.5 py-0.5 text-[11px] font-semibold text-butter-700 hover:bg-butter-100">${label}</button>`;
  return `<div class="mt-2 flex flex-wrap gap-1.5" data-reminder='${esc(JSON.stringify({ block_id: b, title: meta.title || '' }))}'>
    ${b ? btn('data-r-open', 'Open') : ''}${btn('data-r-prep', '✦ Help me prepare')}${btn('data-r-snooze', '⏰ Snooze 1h')}${b ? btn('data-r-done', '✓ Mark done') : ''}
  </div>`;
}

// Pictures the agent made (selfies, drawings): click to open full size.
function picturesHTML(urls) {
  if (!urls?.length) return '';
  return `<div class="pics mt-2 flex flex-wrap gap-2">${urls.map(u => `
    <button data-lightbox="${esc(u)}" class="block rounded-2xl overflow-hidden border border-lavender-100 shadow-sm hover:shadow-md transition max-w-[16rem]">
      <img src="${esc(u)}" alt="Picture from your agent" loading="lazy" class="block max-h-72 w-auto object-cover">
    </button>`).join('')}</div>`;
}

// openLightbox (full-size pictures, Save, profile picture) lives in gallery.js.

function agentBubble(text, meta, at) {
  if (meta?.agent_job) {   // a result of one of the agent's scheduled tasks (Agent's Work)
    return `<div class="msg agent-job flex gap-2">
      ${agentAvatarHTML('w-6 h-6', 'text-[11px]')}
      <div class="min-w-0 flex-1 rounded-2xl rounded-tl-md border ${meta.failed ? 'border-blush-200 bg-blush-50' : 'border-lavender-200 bg-lavender-50/60'} px-3 py-2">
        <a href="#/work" class="mb-1 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-lavender-700 hover:underline" title="Open Agent’s Work">🔁 ${esc(meta.title || 'Scheduled task')}</a>
        ${toolChips(meta.tools)}
        <div class="body chat-md text-sm text-slate-700 break-words">${md(text)}</div>
        ${picturesHTML(meta.images)}
        <div class="foot">${metaLine(meta, at)}</div>
      </div>
    </div>`;
  }
  if (meta?.reminder) {
    return `<div class="msg reminder flex gap-2">
      <span class="w-6 h-6 shrink-0 rounded-full bg-butter-200 flex items-center justify-center text-[12px]">⏰</span>
      <div class="min-w-0 flex-1 rounded-2xl rounded-tl-md border border-butter-200 bg-butter-50 px-3 py-2">
        <div class="body chat-md text-sm text-slate-700 break-words">${md(text)}</div>
        ${reminderActions(meta)}
        <div class="foot">${metaLine(meta, at)}</div>
      </div>
    </div>`;
  }
  return `<div class="msg flex gap-2">
      ${agentAvatarHTML('w-6 h-6', 'text-[11px]')}
      <div class="min-w-0 flex-1">
        ${toolChips(meta?.tools)}
        <div class="body chat-md text-sm text-slate-700 break-words">${text ? md(text) : ''}</div>
        ${picturesHTML(meta?.images)}
        <div class="foot">${metaLine(meta, at)}</div>
      </div>
    </div>`;
}

function emptyChat() {
  return `<div class="empty text-center text-sm text-slate-400 py-10">
      <div class="mx-auto mb-3 w-12 h-12 rounded-full bg-gradient-to-br from-lilac-300 via-baby-300 to-lavender-300 text-white flex items-center justify-center text-lg">✦</div>
      <p class="font-semibold text-slate-600">Hi${chat.userName ? `, ${esc(chat.userName)}` : ''}! I’m ${esc(chat.agentName)}.</p>
      <p class="mt-1">Ask me what to do today, what’s left in a project, or to take a task off your hands.</p>
      ${chat.awakened === false ? '<a href="#/awaken" class="mt-3 inline-block rounded-full bg-gradient-to-r from-blush-200 via-lilac-200 to-baby-200 px-4 py-1.5 text-xs font-bold text-slate-800">✦ Awaken me first</a>' : ''}
      <div class="mt-4 flex flex-wrap justify-center gap-1.5">
        ${['What should I do today?', 'What’s left in my projects?', 'Remember that I prefer short answers'].map(q => `<button data-suggest class="rounded-full border border-lavender-200 px-3 py-1 text-xs text-slate-600 hover:bg-lavender-50">${q}</button>`).join('')}
      </div>
    </div>`;
}

const scrollDown = () => { chat.log.scrollTop = chat.log.scrollHeight; };
const nearBottom = (slack = 120) => chat.log.scrollHeight - chat.log.scrollTop - chat.log.clientHeight < slack;
const bubble = m => m.role === 'user' ? userBubble(m.text, m.meta, undefined, m.created_at) : agentBubble(m.text, m.meta, m.created_at);
const bubbles = list => list.map(bubble).join('');

async function loadHistory() {
  const hist = await api('GET', `/api/chat/history?limit=${PAGE}`);
  chat.log.innerHTML = hist.length ? bubbles(hist) : emptyChat();
  chat.loaded = true;
  chat.firstId = hist[0]?.id ?? null;
  chat.hasMore = hist.length === PAGE;
  if (hist.length) chat.lastId = Math.max(chat.lastId ?? 0, hist[hist.length - 1].id);
  scrollDown();
  markSeen();
  fillUp();
  return hist;
}

// Change what's above the view without moving what you're reading.
function keepPosition(change) {
  const height = chat.log.scrollHeight, top = chat.log.scrollTop;
  change();
  chat.log.scrollTop = top + (chat.log.scrollHeight - height);
}

// Prepend the next page of older messages.
async function loadOlder() {
  if (chat.loadingOlder || !chat.hasMore || chat.firstId == null) return;
  chat.loadingOlder = true;
  keepPosition(() => chat.log.insertAdjacentHTML('afterbegin', '<div class="older-loading text-center text-[11px] text-slate-400 py-1">Loading earlier messages…</div>'));
  try {
    const older = await api('GET', `/api/chat/history?limit=${PAGE}&before=${chat.firstId}`);
    keepPosition(() => {
      chat.log.querySelector('.older-loading')?.remove();
      chat.log.insertAdjacentHTML('afterbegin', bubbles(older));
    });
    chat.hasMore = older.length === PAGE;
    if (older.length) chat.firstId = older[0].id;
  } catch {
    keepPosition(() => chat.log.querySelector('.older-loading')?.remove());
  } finally {
    chat.loadingOlder = false;
  }
  fillUp();
}
// A short first page may not fill the panel, leaving nothing to scroll: keep loading until it does.
function fillUp() {
  if (chat.hasMore && chat.log.scrollHeight <= chat.log.clientHeight + 40) loadOlder();
}

function updateLatest() {
  const away = !nearBottom(300);
  chat.latest.classList.toggle('hidden', !away);
  if (!away) chat.latest.querySelector('[data-dot]').classList.add('hidden');
}
chat.log.addEventListener('scroll', () => {
  if (chat.log.scrollTop < 300) loadOlder();
  updateLatest();
}, { passive: true });
chat.latest.onclick = () => chat.log.scrollTo({ top: chat.log.scrollHeight, behavior: 'smooth' });
// An agent picture (no fixed height) above the view finishing loading would push what you're reading down.
chat.log.addEventListener('load', e => {
  const img = e.target;
  if (img.tagName !== 'IMG' || !img.closest('.pics')) return;
  if (img.getBoundingClientRect().bottom <= chat.log.getBoundingClientRect().top + 1) chat.log.scrollTop += img.offsetHeight;
  else if (nearBottom(img.offsetHeight + 120)) { scrollDown(); updateLatest(); }   // was at the bottom: stay there
}, true);

// ---------------------------------------------------------------- unread & reminders arriving
function markSeen() {
  if (chat.panel.classList.contains('hidden') || document.hidden || chat.lastId == null) return;
  api('POST', '/api/chat/seen', { last_id: chat.lastId }).catch(() => {});
  setChatBadge(0);
}
function setChatBadge(n) {
  const badge = document.getElementById('chat-badge');
  badge.textContent = n;
  badge.classList.toggle('hidden', !n);
}
function plain(text) { return text.replace(/[*_`>#]/g, '').replace(/\s+/g, ' ').trim(); }

// Called by app.js's poll loop with /api/version.
window.onChatVersion = async v => {
  setChatBadge(chat.panel.classList.contains('hidden') ? v.chat_unread : 0);
  if (chat.lastId === null) { chat.lastId = v.chat_last_id; return; }   // first look: nothing to announce
  if (chat.running || v.chat_last_id <= chat.lastId) return;
  const fresh = await api('GET', `/api/chat/history?after=${chat.lastId}`);
  chat.lastId = v.chat_last_id;
  if (chat.panel.classList.contains('hidden')) chat.loaded = false;   // reload the history on next open
  else {
    const follow = nearBottom();
    chat.log.querySelector('.empty')?.remove();
    for (const m of fresh) {
      const el = document.createElement('div');
      el.innerHTML = bubble(m);
      chat.log.appendChild(el);
      animate(el, 'pop-in');
    }
    if (follow) scrollDown();   // reading older messages: stay put, the ⌄ button gets a dot
    else if (fresh.length) chat.latest.querySelector('[data-dot]').classList.remove('hidden');
    updateLatest();
    markSeen();
  }
  // One sound per batch: the newest message's. Not for a reply a chat on this device is still streaming:
  // that tab plays it when the reply is done (the server saves it a moment before).
  const heard = fresh.filter(m => m.role === 'assistant').pop();
  if (heard && !(Number(localGet('nestwork.streamingUntil') || 0) > Date.now() && !heard.meta?.reminder && !heard.meta?.agent_job)) notifySound(heard.meta, heard.id);
  for (const m of fresh.filter(m => m.meta?.reminder || m.meta?.agent_job)) {
    const icon = m.meta.agent_job ? '🔁' : '⏰', title = m.meta.title || (m.meta.agent_job ? 'Scheduled task' : 'Reminder');
    if (chat.panel.classList.contains('hidden')) toast(`${icon} ${title}: ${m.meta.agent_job ? 'your agent’s result is in the chat.' : 'open the chat to see it.'}`, 'info');
    if (('Notification' in window) && Notification.permission === 'granted' && (document.hidden || chat.panel.classList.contains('hidden'))) {
      const n = new Notification(`${icon} ${title}`, { body: plain(m.text).slice(0, 180), tag: `nestwork-${m.id}` });
      n.onclick = () => { window.focus(); setChatOpen(true); n.close(); };
    }
  }
};
document.addEventListener('visibilitychange', markSeen);

// ---------------------------------------------------------------- open / close
// Opening slides the panel in; closing slides it out first and hides it after (body.chat-open is the
// source of truth for "is the chat open" while it's on its way out).
function setChatOpen(open, { focus = true } = {}) {
  const shown = document.body.classList.contains('chat-open');
  document.body.classList.toggle('chat-open', open);
  document.getElementById('chat-btn').classList.toggle('bg-lavender-100', open);
  localSet('nestwork.chat', open ? '1' : '0');
  clearTimeout(chat.closing);
  if (!open) {
    if (shown && !chat.panel.classList.contains('hidden')) {
      animate(chat.panel, 'panel-out');
      chat.closing = setTimeout(() => chat.panel.classList.add('hidden'), 280);
    } else chat.panel.classList.add('hidden');
    return;
  }
  chat.panel.classList.remove('hidden');
  if (open) {
    setChatBadge(0);
    if (!shown) animate(chat.panel, 'slide-in');
    if (!chat.loaded) loadHistory(); else markSeen();
    if (focus) setTimeout(() => chat.input.focus(), 50);
  }
}
document.getElementById('chat-btn').onclick = () => setChatOpen(!document.body.classList.contains('chat-open'));
document.getElementById('chat-close').onclick = () => setChatOpen(false);

async function refreshChatProfile() {
  try {
    const p = await api('GET', '/api/agent/profile');
    chat.agentName = p.agent_name || 'Agent';
    chat.userName = p.user_name || '';
    chat.awakened = p.awakened;
    if (gal.avatar !== (p.avatar || '')) { gal.avatar = p.avatar || ''; refreshAvatars(); }
    document.getElementById('chat-name').textContent = chat.agentName;
    document.getElementById('chat-name').title = `Open ${chat.agentName}’s gallery`;
    if (!chat.running) chat.status.textContent = p.claude_found ? 'Ready' : '⚠️ Claude Code (the `claude` command) wasn’t found';
    const empty = chat.log.querySelector('.empty');
    if (empty) empty.outerHTML = emptyChat();
  } catch { /* offline: keep the old name */ }
}
window.refreshChatProfile = refreshChatProfile;

// ---------------------------------------------------------------- sending
function setRunning(on) {
  chat.running = on;
  chat.send.textContent = on ? '■' : '↑';
  chat.send.setAttribute('aria-label', on ? 'Stop' : 'Send');
  chat.send.title = on ? 'Stop the agent' : 'Send';
  if (!on) chat.status.textContent = 'Ready';
}

// ---------------------------------------------------------------- attachments waiting to be sent
const CHAT_MAX_FILES = 10;

function addChatFiles(list) {
  for (const file of list) {
    if (chat.files.length >= CHAT_MAX_FILES) { toast(`You can attach up to ${CHAT_MAX_FILES} files at a time.`, 'error'); break; }
    if (file.size > FILE_MAX) { toast(`“${file.name}” is over 25 MB.`, 'error'); continue; }
    chat.files.push({ file, url: isPicture(file.type) ? URL.createObjectURL(file) : null });
  }
  renderTray();
  chat.input.focus();
}

function clearChatFiles({ revoke = true } = {}) {
  if (revoke) chat.files.forEach(f => f.url && URL.revokeObjectURL(f.url));
  chat.files = [];
  renderTray();
}

function renderTray() {
  chat.tray.classList.toggle('hidden', !chat.files.length);
  chat.tray.innerHTML = chat.files.map((f, i) => `
    <div class="relative group flex items-center gap-2 rounded-xl border border-lavender-200 bg-white ${f.url ? 'p-0.5' : 'px-2 py-1.5 max-w-[12rem]'}" title="${esc(f.file.name)}">
      ${f.url ? `<img src="${f.url}" alt="${esc(f.file.name)}" class="block h-14 w-14 object-cover rounded-lg">`
        : `<span class="w-7 h-7 shrink-0 flex items-center justify-center rounded bg-lavender-50 text-lavender-700">${ico('paperclip')}</span>
           <span class="min-w-0"><span class="block truncate text-xs font-semibold text-slate-700">${esc(f.file.name)}</span>
           <span class="block text-[10px] text-slate-400">${fileSize(f.file.size)}</span></span>`}
      <button type="button" data-unattach="${i}" class="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-slate-700 text-white text-[10px] leading-5 text-center hover:bg-blush-700" aria-label="Remove ${esc(f.file.name)}">✕</button>
    </div>`).join('');
}

const readAsDataURL = file => new Promise((ok, fail) => {
  const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => fail(new Error(`“${file.name}” couldn’t be read.`)); r.readAsDataURL(file);
});

async function sendMessage(text) {
  text = text.trim();
  if ((!text && !chat.files.length) || chat.running) return;
  const pending = chat.files;
  let files;
  try {
    files = await Promise.all(pending.map(async f => ({ name: f.file.name, mime: f.file.type, data: await readAsDataURL(f.file) })));
  } catch (e) { return toast(e.message, 'error'); }
  chat.log.querySelector('.empty')?.remove();
  chat.input.value = '';
  growInput();
  clearChatFiles({ revoke: false });   // the previews stay in the bubble until the history reloads
  const forceNew = chat.forceNew;
  chat.forceNew = false;
  chat.log.querySelector('.pending-new')?.remove();

  const shown = pending.map(f => ({ name: f.file.name, mime: f.file.type, size: f.file.size, href: f.url || URL.createObjectURL(f.file) }));
  const userEl = document.createElement('div');
  const sentAt = new Date();
  userEl.innerHTML = userBubble(text, null, shown, sentAt);
  chat.log.appendChild(userEl);
  animate(userEl, 'pop-in');
  await streamReply({ message: text, new_chat: forceNew, files }, session => { userEl.innerHTML = userBubble(text, session, shown, sentAt); });
}

// The agent's very first words after the awakening: written live by the agent, not a canned greeting.
async function startAwakeningChat() {
  await refreshChatProfile();
  setChatOpen(true, { focus: false });
  await new Promise(r => setTimeout(r, 300));
  chat.log.querySelector('.empty')?.remove();
  chat.log.insertAdjacentHTML('beforeend', `<div class="flex items-center gap-2 py-1 text-[11px] text-lilac-700"><span class="h-px flex-1 bg-lilac-200"></span>✦ ${esc(chat.agentName)} is waking up<span class="h-px flex-1 bg-lilac-200"></span></div>`);
  animate(chat.log.lastElementChild, 'pop-in');
  await streamReply({ awakening: true }, null, 'Waking up…');
  chat.input.focus();
}
window.startAwakeningChat = startAwakeningChat;

// Stream one agent reply into a new bubble. `onSession` gets the session decision (for the user's bubble).
async function streamReply(body, onSession, firstStatus = 'Thinking…') {
  const reply = document.createElement('div');
  reply.innerHTML = agentBubble('', null, false);
  reply.querySelector('.body').innerHTML = '<span class="typing-dots text-lg leading-none text-slate-400"><span>•</span><span>•</span><span>•</span></span>';
  chat.log.appendChild(reply);
  animate(reply, 'pop-in');
  scrollDown();
  setRunning(true);
  chat.status.textContent = firstStatus;
  // Tells this device's other tabs (and this tab's own poll) a reply is coming here, so they don't sound it too.
  const streaming = setInterval(() => localSet('nestwork.streamingUntil', String(Date.now() + 15e3)), 5e3);
  localSet('nestwork.streamingUntil', String(Date.now() + 15e3));

  let text_ = '', tools = [], images = [], frame = null;
  const paint = () => {
    frame = null;
    reply.querySelector('.body').innerHTML = md(text_) || reply.querySelector('.body').innerHTML;
    if (nearBottom()) scrollDown();
    updateLatest();
  };
  const handle = (event, data) => {
    if (event === 'session') {
      onSession?.(data);
    } else if (event === 'delta') {
      text_ += data.text;
      chat.status.textContent = 'Writing…';
      frame ??= requestAnimationFrame(paint);
    } else if (event === 'tool') {
      tools.push(data);
      chat.status.textContent = `${data.label}…`;
      if (text_ && !text_.endsWith('\n\n')) text_ += '\n\n';
      const chips = reply.querySelector('.tools');
      const html = toolChips(tools);
      if (chips) chips.outerHTML = html; else reply.querySelector('.body').insertAdjacentHTML('beforebegin', html);
      animate(reply.querySelector('.tools').lastElementChild, 'pop-in');
    } else if (event === 'image') {
      images.push(data.url);
      const pics = reply.querySelector('.pics'), html = picturesHTML(images);
      if (pics) pics.outerHTML = html; else reply.querySelector('.body').insertAdjacentHTML('afterend', html);
      animate(reply.querySelector('.pics').lastElementChild, 'pop-in');
      if (nearBottom(400)) scrollDown();
    } else if (event === 'done') {
      reply.innerHTML = agentBubble(data.text || (data.stopped ? '_Stopped._' : ''), data);
      if (!data.stopped && (data.text || data.images?.length)) notifySound({}, data.id ?? null);
    } else if (event === 'error') {
      reply.innerHTML = agentBubble(`⚠️ ${data.message}`, null);
    }
  };

  try {
    const res = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) throw new Error((await res.json().catch(() => null))?.detail || 'The chat failed to start.');
    const reader = res.body.getReader(), decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = chunk.match(/^event: (.*)$/m)?.[1], data = chunk.match(/^data: (.*)$/m)?.[1];
        if (ev && data) handle(ev, JSON.parse(data));
      }
    }
  } catch (e) {
    handle('error', { message: e.message });
  } finally {
    clearInterval(streaming);
    localSet('nestwork.streamingUntil', String(Date.now() + 5e3));   // the poll may still see the saved reply
    setRunning(false);
    if (nearBottom(200)) scrollDown();
    updateLatest();
    try {
      chat.lastId = (await api('GET', '/api/version')).chat_last_id;
      localSet('nestwork.soundedId', String(Math.max(chat.lastId, Number(localGet('nestwork.soundedId') || 0))));   // other tabs: already heard
      markSeen();
    } catch { /* next poll catches up */ }
    poll(true);   // show what the agent changed right away
  }
}

chat.form.onsubmit = e => {
  e.preventDefault();
  if (chat.running) { api('POST', '/api/chat/stop'); chat.status.textContent = 'Stopping…'; return; }
  sendMessage(chat.input.value);
};
const growInput = () => { chat.input.style.height = 'auto'; chat.input.style.height = `${Math.min(chat.input.scrollHeight, 160)}px`; };
chat.input.addEventListener('input', growInput);
chat.fileInput.onchange = () => { addChatFiles([...chat.fileInput.files]); chat.fileInput.value = ''; };
chat.tray.addEventListener('click', e => {
  const x = e.target.closest('[data-unattach]');
  if (!x) return;
  const [f] = chat.files.splice(+x.dataset.unattach, 1);
  if (f?.url) URL.revokeObjectURL(f.url);
  renderTray();
});
chat.input.addEventListener('paste', e => {
  const files = [...(e.clipboardData?.files || [])];
  if (!files.length) return;
  e.preventDefault();   // a pasted screenshot becomes an attachment, not its file name
  addChatFiles(files);
});
chat.panel.addEventListener('dragover', e => {
  if (!e.dataTransfer.types.includes('Files')) return;
  e.preventDefault();
  chat.form.classList.add('ring-2', 'ring-inset', 'ring-lavender-300');
});
chat.panel.addEventListener('dragleave', e => { if (!chat.panel.contains(e.relatedTarget)) chat.form.classList.remove('ring-2', 'ring-inset', 'ring-lavender-300'); });
chat.panel.addEventListener('drop', e => {
  chat.form.classList.remove('ring-2', 'ring-inset', 'ring-lavender-300');
  if (!e.dataTransfer.files.length) return;
  e.preventDefault();
  addChatFiles([...e.dataTransfer.files]);
});
chat.input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); chat.form.requestSubmit(); }
  if (e.key === 'Escape') e.stopPropagation();   // don't close an open edit panel while typing here
});
chat.log.addEventListener('click', async e => {
  const pic = e.target.closest('[data-lightbox]');
  if (pic) return loadGallery().catch(() => {}).then(() => openLightbox(pic.dataset.lightbox, { list: gal.items.some(i => i.url === pic.dataset.lightbox) ? gal.items : null }));
  // A picture you sent: the lightbox too, browsing that message's pictures (⌘/Ctrl-click still opens a tab).
  const sent = e.target.closest('[data-sent-pic]');
  if (sent && !(e.metaKey || e.ctrlKey || e.shiftKey || e.button)) {
    e.preventDefault();
    const msg = sent.closest('.msg'), at = msg?.querySelector('time')?.getAttribute('datetime') || new Date().toISOString();
    const list = [...(msg || sent).querySelectorAll('[data-sent-pic]')].map(a => ({ url: a.getAttribute('href'), name: a.dataset.sentPic, upload: true, created_at: at }));
    return openLightbox(sent.getAttribute('href'), { list });
  }
  const s = e.target.closest('[data-suggest]');
  if (s) return sendMessage(s.textContent);
  const box = e.target.closest('[data-reminder]');
  if (!box) return;
  const r = JSON.parse(box.dataset.reminder);
  if (e.target.closest('[data-r-open]')) go(r.block_id);
  else if (e.target.closest('[data-r-prep]')) sendMessage(`Help me prepare for “${r.title}”.`);
  else if (e.target.closest('[data-r-snooze]')) {
    const at = new Date(Date.now() + 3600e3);
    const local = new Date(at.getTime() - at.getTimezoneOffset() * 60e3).toISOString().slice(0, 16);
    if (await attempt(() => api('POST', '/api/reminders', { text: r.title || 'Reminder', remind_at: local, block_id: r.block_id }))) {
      toast(`I’ll remind you again at ${at.toTimeString().slice(0, 5)}.`, 'success');
      box.querySelector('[data-r-snooze]').outerHTML = '<span class="text-[11px] text-butter-700">Snoozed ✓</span>';
    }
  } else if (e.target.closest('[data-r-done]')) {
    const data = await api('GET', `/api/blocks/${r.block_id}`).catch(() => null);
    if (!data) return toast('That to-do no longer exists.', 'error');
    const last = data.stages[data.stages.length - 1];
    if (await attempt(() => api('POST', `/api/blocks/${r.block_id}/move`, { status: last.key }))) {
      box.querySelector('[data-r-done]').outerHTML = `<span class="text-[11px] text-mint-700">Moved to “${esc(last.label)}” ✓</span>`;
      poll(true);
    }
  }
});
document.getElementById('chat-new').onclick = () => {
  if (chat.running) return;
  chat.forceNew = true;
  chat.log.querySelector('.pending-new')?.remove();
  chat.log.insertAdjacentHTML('beforeend', `<div class="pending-new flex items-center gap-2 py-1 text-[11px] text-lavender-700"><span class="h-px flex-1 bg-lavender-200"></span>✦ Your next message starts a new chat<span class="h-px flex-1 bg-lavender-200"></span></div>`);
  animate(chat.log.lastElementChild, 'pop-in');
  scrollDown();
  chat.input.focus();
};

refreshChatProfile();
if (localGet('nestwork.chat') === '1') setChatOpen(true, { focus: false });
