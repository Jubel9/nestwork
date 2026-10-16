// Notification sounds: presets synthesized with Web Audio (no files), your own uploaded sounds, and the
// Settings → 🔊 Sounds pane. chat.js calls notifySound() when the agent writes: a soft sound while you're
// looking at the chat, another while you're away, and each scheduled task's or reminder's own; the focus
// timer (hud.js) calls playKeyedSound() when a focus session or a break ends. Server side:
// nestwork/sounds.py. Browsers only allow sound after you've clicked or typed on the page once.

const snd = { ctx: null, lib: null, libAt: 0, playing: null };
const SOUND_KEY = 'nestwork.sounds';            // "off" mutes this device
const soundsOn = () => localGet(SOUND_KEY) !== 'off';

function sndCtx() {
  if (!snd.ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    snd.ctx = new AC();
  }
  if (snd.ctx.state === 'suspended') snd.ctx.resume().catch(() => {});
  return snd.ctx;
}
// Unlock audio on the first click or key press (browsers block sound until then).
['pointerdown', 'keydown', 'touchend'].forEach(t => document.addEventListener(t, () => sndCtx(), { once: true, capture: true }));

// ---------------------------------------------------------------- presets
// sndTone(): one note with an attack/decay envelope, optional pitch glide, and an optional lowpass for plucks.
function sndTone(ctx, out, { f, f2 = null, t = 0, dur = 0.4, type = 'sine', gain = 0.5, attack = 0.005, lp = null }) {
  const at = ctx.currentTime + 0.02 + t;
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(f, at);
  if (f2) o.frequency.exponentialRampToValueAtTime(f2, at + dur * 0.8);
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(gain, at + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  let node = o;
  if (lp) { const fl = ctx.createBiquadFilter(); fl.type = 'lowpass'; fl.frequency.setValueAtTime(lp, at); fl.frequency.exponentialRampToValueAtTime(Math.max(200, lp / 6), at + dur); o.connect(fl); node = fl; }
  node.connect(g).connect(out);
  o.start(at); o.stop(at + dur + 0.05);
  return at + dur;
}
const NOTE = n => 440 * 2 ** ((n - 69) / 12);   // MIDI note → Hz
const PRESET_SOUNDS = {
  pop: (c, o) => sndTone(c, o, { f: 620, f2: 280, dur: 0.12, gain: 0.7 }),
  bubble: (c, o) => { sndTone(c, o, { f: 320, f2: 900, dur: 0.1, gain: 0.5 }); sndTone(c, o, { f: 420, f2: 1200, t: 0.09, dur: 0.12, gain: 0.45 }); },
  chime: (c, o) => { sndTone(c, o, { f: NOTE(81), dur: 1.1, type: 'triangle', gain: 0.45 }); sndTone(c, o, { f: NOTE(88), t: 0.13, dur: 1.3, type: 'triangle', gain: 0.4 }); },
  marimba: (c, o) => [72, 76, 79].forEach((n, i) => { sndTone(c, o, { f: NOTE(n), t: i * 0.11, dur: 0.45, gain: 0.55 }); sndTone(c, o, { f: NOTE(n) * 4, t: i * 0.11, dur: 0.08, gain: 0.08 }); }),
  bell: (c, o) => [[1, 0.5, 1.8], [2, 0.18, 1.2], [2.76, 0.12, 0.9], [5.4, 0.05, 0.5]].forEach(([m, g, d]) => sndTone(c, o, { f: 660 * m, dur: d, gain: g, attack: 0.002 })),
  ping: (c, o) => sndTone(c, o, { f: 1760, dur: 0.5, gain: 0.45, attack: 0.002 }),
  glass: (c, o) => { sndTone(c, o, { f: NOTE(96), dur: 0.6, type: 'triangle', gain: 0.3 }); sndTone(c, o, { f: NOTE(103), t: 0.07, dur: 0.7, type: 'triangle', gain: 0.22 }); },
  harp: (c, o) => [60, 64, 67, 72, 76].forEach((n, i) => sndTone(c, o, { f: NOTE(n), t: i * 0.07, dur: 0.9, type: 'triangle', gain: 0.35 })),
  koto: (c, o) => [76, 77, 81, 83].forEach((n, i) => sndTone(c, o, { f: NOTE(n), t: i * 0.16, dur: 0.8, type: 'sawtooth', gain: 0.22, lp: 3200, attack: 0.002 })),
  arcade: (c, o) => { sndTone(c, o, { f: NOTE(83), dur: 0.09, type: 'square', gain: 0.18 }); sndTone(c, o, { f: NOTE(88), t: 0.09, dur: 0.25, type: 'square', gain: 0.18 }); },
  soft: (c, o) => { sndTone(c, o, { f: 220, dur: 1.8, gain: 0.35, attack: 0.5 }); sndTone(c, o, { f: 330, t: 0.15, dur: 1.7, gain: 0.2, attack: 0.5 }); },
  sunrise: (c, o) => [60, 64, 67, 71, 74, 79].forEach((n, i) => sndTone(c, o, { f: NOTE(n), t: i * 0.22, dur: 1.2, type: 'triangle', gain: 0.3, attack: 0.08 })),
  alarm: (c, o) => { for (let i = 0; i < 8; i++) sndTone(c, o, { f: i % 2 ? 1100 : 880, t: i * 0.18 + Math.floor(i / 4) * 0.35, dur: 0.15, type: 'square', gain: 0.16, attack: 0.003 }); },
};

function stopSound() {
  if (snd.playing) { try { snd.playing.pause(); } catch { /* gone */ } snd.playing = null; }
}
// Play "preset:<key>" | "file:<id>" | "none" at a volume (0–1).
function playNotifySound(sound, volume = 0.8) {
  if (!sound || sound === 'none' || volume <= 0) return;
  const [kind, ref] = sound.split(':');
  if (kind === 'preset' && PRESET_SOUNDS[ref]) {
    const ctx = sndCtx();
    if (!ctx) return;
    const out = ctx.createGain();
    out.gain.value = Math.min(1, volume);
    out.connect(ctx.destination);
    PRESET_SOUNDS[ref](ctx, out);
  } else if (kind === 'file') {
    stopSound();
    const a = new Audio(`/api/sounds/files/${ref}`);
    a.volume = Math.min(1, volume);
    snd.playing = a;
    a.play().catch(() => {});
    // A long file (a song as an alarm) stops after a minute, or on your next click or key press.
    setTimeout(() => { if (snd.playing === a) stopSound(); }, 60e3);
    setTimeout(() => ['pointerdown', 'keydown'].forEach(t => document.addEventListener(t, () => { if (snd.playing === a) stopSound(); }, { once: true, capture: true })), 400);
  }
}

async function soundLib(force = false) {
  if (force || !snd.lib || Date.now() - snd.libAt > 30e3) {
    try { snd.lib = await api('GET', '/api/sounds'); snd.libAt = Date.now(); } catch { /* keep the last one */ }
  }
  return snd.lib || { presets: [], files: [], notifications: [] };
}

// Is the user looking at the chat right now? (Panel open, this tab in front, the window focused.)
function lookingAtChat() {
  return !document.hidden && document.hasFocus() && !document.getElementById('chat')?.classList.contains('hidden');
}

// Play the notification for a message from the agent. meta: the message's meta (agent_job, notify, reminder).
// id: the message id, so two open tabs on this device don't both play it.
async function notifySound(meta = {}, id = null) {
  if (!soundsOn()) return;
  const looking = lookingAtChat();
  if (id != null) {
    if (Number(localGet('nestwork.soundedId') || 0) >= id) return;
    localSet('nestwork.soundedId', String(id));
    // Other pages (another browser or address, the phone) hear it too: the server lets only the first one play.
    // The page you're looking at asks right away, the rest a moment later, so the sound comes from where you are.
    if (!looking && !document.hasFocus()) await new Promise(r => setTimeout(r, 1500));
    const ok = await api('POST', `/api/sounds/claim/${id}`).then(r => r.play).catch(() => true);
    if (!ok) return;
  }
  const lib = await soundLib();
  const byKey = k => lib.notifications.find(n => n.key === k);
  let n;
  if (meta?.agent_job) {
    if (meta.notify === 'none') return;
    n = (meta.notify && lib.notifications.find(x => String(x.id) === String(meta.notify))) || byKey('task');
  } else if (meta?.reminder) n = byKey('reminder');
  else n = byKey(looking ? 'chat_open' : 'chat_away');
  if (n) playNotifySound(n.sound, n.volume);
}

// Play a built-in notification by its key (the focus timer's "focus_end" / "break_end"). A preset can play
// `repeat` times, 1.8 s apart; your own files play once.
async function playKeyedSound(key, repeat = 1) {
  if (!soundsOn()) return;
  const n = (await soundLib()).notifications.find(x => x.key === key);
  if (!n) return;
  const times = n.sound.startsWith('preset:') ? Math.max(1, repeat) : 1;
  for (let i = 0; i < times; i++) setTimeout(() => playNotifySound(n.sound, n.volume), i * 1800);
}

// <option>s for a sound picker: presets, your uploads, None.
function soundOptions(lib, current) {
  const opt = (v, l) => `<option value="${esc(v)}" ${v === current ? 'selected' : ''}>${esc(l)}</option>`;
  return `<optgroup label="Presets">${lib.presets.map(p => opt(`preset:${p.key}`, p.name)).join('')}</optgroup>
    ${lib.files.length ? `<optgroup label="Your sounds">${lib.files.map(f => opt(`file:${f.id}`, f.name)).join('')}</optgroup>` : ''}
    ${opt('none', 'None (silent)')}`;
}
function soundName(lib, sound) {
  if (sound === 'none') return 'silent';
  const [kind, ref] = sound.split(':');
  return kind === 'preset' ? lib.presets.find(p => p.key === ref)?.name || ref : lib.files.find(f => String(f.id) === ref)?.name || 'a deleted sound';
}

// ---------------------------------------------------------------- Settings → 🔊 Sounds
async function soundsPane(box) {
  const lib = await soundLib(true);
  const field = 'rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-lavender-300';
  const testBtn = attr => `<button ${attr} class="shrink-0 w-8 h-8 rounded-full border border-lavender-200 text-lavender-700 hover:bg-lavender-50" title="Play it" aria-label="Play it">▶</button>`;
  const builtIn = lib.notifications.filter(n => n.key), mine = lib.notifications.filter(n => !n.key);
  const row = n => `<li data-notif="${n.id}" class="rounded-xl border border-slate-200 p-3">
      <div class="flex flex-wrap items-center gap-2">
        ${n.key ? `<span class="flex-1 min-w-[10rem]"><span class="block text-sm font-semibold text-slate-700">${esc(n.name)}</span><span class="block text-[11px] text-slate-500">${esc(n.help)}</span></span>`
          : `<input data-n-name maxlength="60" value="${esc(n.name)}" class="${field} flex-1 min-w-[10rem] font-semibold" aria-label="Name">`}
        <select data-n-sound class="${field} w-40" aria-label="Sound">${soundOptions(lib, n.sound)}</select>
        <input data-n-volume type="range" min="0" max="1" step="0.05" value="${n.volume}" class="w-24 accent-lavender-700" aria-label="Volume" title="Volume">
        ${testBtn('data-n-test')}
        ${n.key ? '' : '<button data-n-del class="shrink-0 p-1.5 rounded-lg text-slate-400 hover:text-blush-700 hover:bg-blush-50" title="Delete" aria-label="Delete">🗑</button>'}
      </div>
    </li>`;
  box.innerHTML = `
    <div class="space-y-4">
      <section class="rounded-2xl border border-slate-200 p-4">
        <label class="flex items-start gap-3 cursor-pointer">
          <input id="snd-on" type="checkbox" class="mt-1 rounded" ${soundsOn() ? 'checked' : ''}>
          <span><span class="block text-sm font-semibold text-slate-700">🔊 Play sounds on this device</span>
          <span class="block text-xs text-slate-500">Each computer and phone has its own switch. Sounds play while a Nestwork page is open (a tab, the installed app or your phone). Browsers only allow sound after you’ve clicked the page once.</span></span>
        </label>
        <p class="mt-3 text-[11px] font-bold uppercase tracking-wider text-slate-400">Presets: tap to hear</p>
        <div class="mt-1.5 flex flex-wrap gap-1.5">${lib.presets.map(p => `<button data-preset="${p.key}" class="rounded-full border border-lavender-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-lavender-50">▶ ${esc(p.name)}</button>`).join('')}</div>
      </section>

      <section class="rounded-2xl border border-slate-200 p-4">
        <h3 class="text-sm font-bold text-slate-700">🔔 Notifications</h3>
        <p class="text-xs text-slate-500">What plays for each kind of message and when the focus timer runs out. Add your own (e.g. “Wake up”, “Sleep”) and pick them for scheduled tasks in <a href="#/work" data-close class="text-lavender-700 underline">Agent’s Work</a>.</p>
        <ul class="mt-3 space-y-2">${builtIn.map(row).join('')}</ul>
        ${mine.length ? `<p class="mt-4 text-[11px] font-bold uppercase tracking-wider text-slate-400">Yours</p><ul class="mt-1.5 space-y-2">${mine.map(row).join('')}</ul>` : ''}
        <form id="snd-add" class="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-dashed border-lavender-300 p-3">
          <input name="name" maxlength="60" placeholder="New notification, e.g. Wake up" class="${field} flex-1 min-w-[10rem]">
          <select name="sound" class="${field} w-40" aria-label="Sound">${soundOptions(lib, 'preset:sunrise')}</select>
          ${testBtn('type="button" data-add-test')}
          <button class="rounded-full bg-lavender-700 hover:bg-lavender-800 text-white text-xs font-semibold px-3 py-1.5">+ Add</button>
        </form>
      </section>

      <section class="rounded-2xl border border-slate-200 p-4">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div><h3 class="text-sm font-bold text-slate-700">📁 Your sounds</h3>
          <p class="text-xs text-slate-500">Upload .mp3, .m4a, .wav or .ogg files (up to 5 MB each), then pick them above. Long ones stop after a minute or when you click.</p></div>
          <label class="shrink-0 cursor-pointer rounded-full border border-lavender-300 px-3 py-1.5 text-xs font-semibold text-lavender-700 hover:bg-lavender-50">${ico('upload')} Upload<input id="snd-file" type="file" accept="audio/*,.mp3,.m4a,.wav,.ogg" multiple class="hidden"></label>
        </div>
        <ul class="mt-3 space-y-1.5">${lib.files.map(f => `<li data-file-id="${f.id}" class="flex items-center gap-2">
            ${testBtn('data-f-test')}
            <input data-f-name maxlength="60" value="${esc(f.name)}" class="${field} flex-1 min-w-0" aria-label="Sound name">
            <span class="shrink-0 text-[11px] text-slate-400">${Math.max(1, Math.round(f.size / 1024))} KB</span>
            <button data-f-del class="shrink-0 p-1.5 rounded-lg text-slate-400 hover:text-blush-700 hover:bg-blush-50" title="Delete" aria-label="Delete">🗑</button>
          </li>`).join('') || '<li class="text-xs text-slate-400">No sounds uploaded yet.</li>'}</ul>
      </section>
    </div>`;

  const reload = () => soundsPane(box);
  const $ = q => box.querySelector(q);
  $('#snd-on').onchange = e => { localSet(SOUND_KEY, e.target.checked ? 'on' : 'off'); if (e.target.checked) playNotifySound('preset:pop', 0.5); };
  box.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => playNotifySound(`preset:${b.dataset.preset}`, 0.8));

  box.querySelectorAll('[data-notif]').forEach(li => {
    const id = li.dataset.notif, sel = li.querySelector('[data-n-sound]'), vol = li.querySelector('[data-n-volume]');
    const save = async body => { if (await attempt(() => api('PATCH', `/api/notifications/${id}`, body))) soundLib(true); };
    li.querySelector('[data-n-test]').onclick = () => playNotifySound(sel.value, Number(vol.value));
    sel.onchange = () => { playNotifySound(sel.value, Number(vol.value)); save({ sound: sel.value }); };
    vol.onchange = () => { playNotifySound(sel.value, Number(vol.value)); save({ volume: Number(vol.value) }); };
    const name = li.querySelector('[data-n-name]');
    if (name) name.onchange = () => name.value.trim() && save({ name: name.value.trim() });
    li.querySelector('[data-n-del]')?.addEventListener('click', async () => {
      if (!confirm(`Delete the “${name.value}” notification? Scheduled tasks using it go back to the default sound.`)) return;
      if (await attempt(() => api('DELETE', `/api/notifications/${id}`))) { await soundLib(true); reload(); }
    });
  });

  const add = $('#snd-add');
  $('[data-add-test]').onclick = () => playNotifySound(add.sound.value, 0.8);
  add.onsubmit = async e => {
    e.preventDefault();
    if (!add.name.value.trim()) return toast('Give the notification a name.', 'error');
    if (await attempt(() => api('POST', '/api/notifications', { name: add.name.value.trim(), sound: add.sound.value }))) {
      toast(`Added “${add.name.value.trim()}”.`, 'success');
      await soundLib(true); reload();
    }
  };

  $('#snd-file').onchange = async e => {
    const files = [...e.target.files];
    for (const f of files) {
      const data = await new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = fail; r.readAsDataURL(f); });
      if (await attempt(() => api('POST', '/api/sounds/files', { name: f.name, data, mime: f.type }))) toast(`Uploaded “${f.name}”.`, 'success');
    }
    await soundLib(true); reload();
  };
  box.querySelectorAll('[data-file-id]').forEach(li => {
    const id = li.dataset.fileId, name = li.querySelector('[data-f-name]');
    li.querySelector('[data-f-test]').onclick = () => playNotifySound(`file:${id}`, 0.8);
    name.onchange = async () => { if (name.value.trim() && await attempt(() => api('PATCH', `/api/sounds/files/${id}`, { name: name.value.trim() }))) { await soundLib(true); reload(); } };
    li.querySelector('[data-f-del]').onclick = async () => {
      if (!confirm(`Delete “${name.value}”? Notifications using it go back to their default sound.`)) return;
      const r = await attempt(() => api('DELETE', `/api/sounds/files/${id}`));
      if (r) { if (r.changed.length) toast(`${r.changed.join(', ')} went back to the default sound.`, 'info'); await soundLib(true); reload(); }
    };
  });
}
