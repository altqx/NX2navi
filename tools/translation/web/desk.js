// The translator's desk (tools/translation/server.py serves it and the API it calls).
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids.flat()) if (k != null) n.append(k);
  return n;
};

const LANG_NAMES = { th: 'Thai', JPja: 'Japanese', USen: 'English', EUen: 'English', USes: 'Spanish', USfr: 'French', USpt: 'Portuguese',
  EUde: 'German', EUes: 'Spanish', EUfr: 'French', EUit: 'Italian' };
// {0.3:XXXX}: a text colour on, ffff back to normal. 0000 marks names, places and items; the others are rarer and
// their colours in the game are not known: the preview gives each its own.
const COLOURS = { '0000': '#ff6b5e', '0100': '#7fb2ff', '0300': '#ffd966', '0400': '#73e0d0', '0500': '#9be07a' };
const THAI_MARKS = new Set('ัิีึืฺุู็่้๊๋์ํ๎');

const S = {
  project: null, files: [], fileFilter: 'all', show: 'all', closed: new Set(JSON.parse(localStorage.deskClosed || '[]')),
  file: null, index: -1, saved: null, glossary: [], searching: false, audio: new Audio(), playing: null,
};

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: `${res.status} ${res.statusText}` }));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// ---- markup

const TOKEN = /\{\{|\}\}|\{[^{}]*\}/g;

function tokens(text) {
  const out = [];
  let at = 0;
  for (const m of text.matchAll(TOKEN)) {
    if (m.index > at) out.push({ text: text.slice(at, m.index) });
    if (m[0] === '{{') out.push({ text: '{' });
    else if (m[0] === '}}') out.push({ text: '}' });
    else out.push({ code: m[0] });
    at = m.index + m[0].length;
  }
  if (at < text.length) out.push({ text: text.slice(at) });
  return out;
}

function codeChip(code) {
  const colour = code.match(/^\{0\.3:([0-9a-f]{4})\}$/);
  const chip = el('span', { className: 'code', textContent: code.slice(1, -1) });
  if (colour) {
    chip.classList.add('colour');
    chip.style.color = colour[1] === 'ffff' ? '' : (COLOURS[colour[1]] || '#d49cff');
    chip.title = colour[1] === 'ffff' ? 'colour back to normal' : 'colour on (the colour shown is a guess)';
  }
  return chip;
}

// The source as it reads: readings as ruby over the characters they belong to, control codes as chips.
function renderSource(text) {
  const frag = document.createDocumentFragment();
  let ruby = null;
  for (const t of tokens(text)) {
    if (t.code) {
      const r = t.code.match(/^\{ruby:(\d+):([^{}]*)\}$/);
      if (r) ruby = { n: +r[1], reading: r[2] };
      else frag.append(codeChip(t.code));
      continue;
    }
    let chars = Array.from(t.text);
    if (ruby) {
      const base = chars.slice(0, ruby.n).join('');
      frag.append(el('ruby', {}, base, el('rt', { textContent: ruby.reading })));
      chars = chars.slice(ruby.n);
      ruby = null;
    }
    if (chars.length) frag.append(chars.join(''));
  }
  return frag;
}

// The translation as the dialogue box shows it: colours applied, other codes as small ticks, icons as a chip.
function renderPreview(text) {
  const frag = document.createDocumentFragment();
  let target = frag;
  for (const t of tokens(text)) {
    if (!t.code) { target.append(t.text); continue; }
    const colour = t.code.match(/^\{0\.3:([0-9a-f]{4})\}$/);
    if (colour) {
      if (colour[1] === 'ffff') target = frag;
      else {
        target = el('span', { style: `color:${COLOURS[colour[1]] || '#d49cff'}` });
        frag.append(target);
      }
    } else if (/^\{ruby:/.test(t.code)) {
      // readings are left out in Thai
    } else if (/^\{U\+/.test(t.code)) {
      target.append(el('span', { className: 'code', textContent: '◉', title: `button icon ${t.code}` }));
    } else {
      target.append(el('span', { className: 'tick', title: t.code }));
    }
  }
  return frag;
}

const clean = (text) => text.replace(/\{\{/g, '{').replace(/\}\}/g, '}').replace(/\{[^{}]*\}/g, '');
const width = (line) => Array.from(line).filter((c) => !THAI_MARKS.has(c)).length;
const codesOf = (text) => [...text.replace(/\{\{|\}\}/g, '').matchAll(/\{[^{}]*\}/g)].map((m) => m[0])
  .filter((c) => !c.startsWith('{ruby:'));

// ---- files

function groupsOf(files) {
  const groups = new Map();
  for (const f of files) {
    const [group, ...rest] = f.path.split('/');
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push({ ...f, name: rest.join('/') });
  }
  return groups;
}

function fileVisible(f) {
  if (S.fileFilter === 'todo') return f.done < f.total;
  if (S.fileFilter === 'voiced') return f.voiced > 0;
  if (S.fileFilter === 'draft') return f.draft > 0;
  return true;
}

// done, then needs review, then drafts, side by side
function progressBar(f) {
  const p = el('div', { className: 'progress' });
  let at = 0;
  for (const [tag, n] of [['i', f.done], ['b', f.fuzzy], ['u', f.draft || 0]]) {
    const w = f.total ? (100 * n) / f.total : 0;
    const seg = el(tag);
    Object.assign(seg.style, { left: `${at}%`, width: `${w}%` });
    p.append(seg);
    at += w;
  }
  return p;
}

function sum(files, key) { return files.reduce((a, f) => a + (f[key] || 0), 0); }

function countText(f) {
  return `${f.done} of ${f.total} done` + (f.draft ? `, ${f.draft} drafts` : '') + (f.fuzzy ? `, ${f.fuzzy} to review` : '');
}

function renderFiles() {
  const list = $('#file-list');
  list.replaceChildren();
  for (const [group, files] of groupsOf(S.files)) {
    const shown = files.filter(fileVisible);
    if (!shown.length) continue;
    const done = files.reduce((a, f) => a + f.done, 0), total = files.reduce((a, f) => a + f.total, 0);
    const head = el('button', { type: 'button', ariaExpanded: String(!S.closed.has(group)) },
      el('span', { textContent: group }), el('span', { textContent: `${done}/${total}` }));
    const ul = el('ul');
    for (const f of shown) {
      const b = el('button', { type: 'button', className: 'file', title: f.path },
        el('span', { className: 'name', textContent: f.name }),
        el('span', { className: 'meta' }, f.voiced ? el('span', { className: 'speaker', textContent: '♪ ', title: `${f.voiced} voiced` }) : null,
          `${f.done}/${f.total}`),
        progressBar(f));
      if (S.file && S.file.path === f.path) b.classList.add('current');
      if (f.total && f.done === f.total) b.classList.add('complete');
      b.addEventListener('click', () => openFile(f.path));
      ul.append(el('li', {}, b));
    }
    const sec = el('div', { className: 'group' + (S.closed.has(group) ? ' closed' : '') }, head, ul);
    head.addEventListener('click', () => {
      S.closed.has(group) ? S.closed.delete(group) : S.closed.add(group);
      localStorage.deskClosed = JSON.stringify([...S.closed]);
      sec.classList.toggle('closed');
      head.ariaExpanded = String(!S.closed.has(group));
    });
    list.append(sec);
  }
  const all = Object.fromEntries(['done', 'fuzzy', 'draft', 'total'].map((k) => [k, sum(S.files, k)]));
  $('#total-progress').replaceWith(Object.assign(progressBar(all), { id: 'total-progress' }));
  $('#total-count').textContent = countText(all).replace(' done', ' messages done');
}

// ---- messages

function shortLabel(ctx) {
  const base = S.file ? S.file.path.split('/').pop() + '_' : '';
  return ctx.startsWith(base) ? ctx.slice(base.length) : ctx;
}

function entryVisible(e) {
  if (S.show === 'todo') return !e.msgstr;
  if (S.show === 'fuzzy' || S.show === 'draft') return e.state === S.show;
  return true;
}

function renderMessages() {
  if (S.searching) return;
  const list = $('#message-list');
  list.replaceChildren();
  const f = S.file;
  if (!f) return;
  $('#file-name').textContent = f.path;
  $('#file-count').textContent = countText(f) + (f.voiced ? ` · ${f.voiced} voiced` : '');
  f.entries.forEach((e, i) => {
    if (!entryVisible(e) && i !== S.index) return;
    const state = e.state;
    const b = el('button', { type: 'button' },
      el('span', { className: 'mark' }),
      el('span', { className: 'label' }, shortLabel(e.ctx), e.voices.length ? el('span', { className: 'speaker', textContent: '♪' }) : null),
      e.msgstr ? el('span', { className: 'line th', lang: 'th', textContent: clean(e.msgstr) })
        : el('span', { className: 'line', lang: 'ja', textContent: clean(e.plain ?? e.msgid) }));
    const li = el('li', { className: [state, i === S.index ? 'current' : ''].join(' ').trim() }, b);
    li.dataset.i = i;
    b.addEventListener('click', () => select(i));
    list.append(li);
  });
  const cur = $('#message-list .current');
  if (cur) cur.scrollIntoView({ block: 'nearest' });
}

async function openFile(path, ctx) {
  if (!(await leave())) return;
  stopVoice();
  try {
    S.file = await api(`/api/file?path=${encodeURIComponent(path)}`);
  } catch (err) {
    $('#editor').replaceChildren(el('div', { className: 'empty' }, el('p', { textContent: `Could not open ${path}: ${err.message}` })));
    return;
  }
  endSearch();
  S.index = -1;
  renderFiles();
  let i = ctx ? S.file.entries.findIndex((e) => e.ctx === ctx) : -1;
  if (i < 0) i = S.file.entries.findIndex((e) => !e.msgstr);
  select(i < 0 ? 0 : i, true);
}

// ---- editor

let E = null;   // the editor's fields

function field(name) { return $(`[data-f="${name}"]`, $('#editor')); }

function select(i, skipLeave) {
  const go = async () => {
    if (!skipLeave && !(await leave())) return;
    if (!S.file || i < 0 || i >= S.file.entries.length) return;
    stopVoice();
    S.index = i;
    const e = S.file.entries[i];
    S.saved = savedOf(e);
    history.replaceState(null, '', `#${encodeURIComponent(S.file.path)}::${encodeURIComponent(e.ctx)}`);
    localStorage.deskLast = JSON.stringify([S.file.path, e.ctx]);
    renderEditor(e);
    renderMessages();
  };
  return go();
}

function savedOf(e) { return { msgstr: e.msgstr, status: e.state || 'done', notes: e.notes }; }

function status() { return $('[data-f="status"] input:checked', $('#editor'))?.value || 'done'; }

function setStatus(value) {
  const r = $(`[data-f="status"] input[value="${value}"]`, $('#editor'));
  if (r) { r.checked = true; update(); }
}

function renderEditor(e) {
  const ed = $('#editor');
  ed.replaceChildren($('#editor-template').content.cloneNode(true), $('#keys-template').content.cloneNode(true));
  field('path').textContent = S.file.path;
  field('ctx').textContent = e.ctx;
  const readings = field('readings');
  readings.checked = localStorage.deskReadings !== 'off';
  const src = field('msgid');
  src.append(renderSource(e.msgid));
  src.classList.toggle('no-readings', !readings.checked);
  readings.addEventListener('change', () => {
    localStorage.deskReadings = readings.checked ? 'on' : 'off';
    $$('.text.ja', ed).forEach((n) => n.classList.toggle('no-readings', !readings.checked));
  });
  if (e.previous) {
    const prev = $('.previous', ed);
    prev.hidden = false;
    field('previous').append(renderSource(e.previous));
  }
  field('ref-name').textContent = LANG_NAMES[S.project.reference] || S.project.reference || 'Reference';
  if (e.ref) field('ref').append(renderSource(e.ref));
  else field('ref').append(el('span', { className: 'muted', textContent: 'No reference text for this message.' }));

  for (const b of $$('.voice', ed)) {
    const lang = b.dataset.voice === 'slot' ? S.project.slot : S.project.reference;
    b.hidden = !(e.voices.includes(lang) || e.voices.includes(''));
    $('.voice-label', b).textContent = `${LANG_NAMES[lang] || lang} voice`;
    b.dataset.lang = lang;
    b.addEventListener('click', () => toggleVoice(b));
  }

  const ta = field('msgstr');
  ta.value = e.msgstr;
  $(`[data-f="status"] input[value="${e.state || 'done'}"]`, ed).checked = true;
  field('note').value = e.notes;
  if (e.notes) $('.note', ed).open = true;
  ta.addEventListener('input', () => { update(); queueCheck(); });
  field('status').addEventListener('change', update);
  $('[data-act="approve"]', ed).addEventListener('click', approve);
  field('note').addEventListener('input', update);
  $('[data-act="save"]', ed).addEventListener('click', () => save(true));
  $('[data-act="prev"]', ed).addEventListener('click', () => move(-1));
  $('[data-act="next"]', ed).addEventListener('click', () => move(1));
  renderChips(e);
  renderGlossary(e);
  update();
  showChecks(e.checks, e.msgstr);
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}

function renderChips(e) {
  const box = field('chips');
  const codes = codesOf(e.msgid);
  const unique = [...new Set(codes)];
  box.replaceChildren(...unique.map((c) => {
    const b = el('button', { type: 'button', textContent: c.slice(1, -1), title: `Insert ${c}` });
    b.dataset.code = c;
    b.addEventListener('click', () => insert(c));
    return b;
  }));
}

function insert(text) {
  const ta = field('msgstr');
  const [a, b] = [ta.selectionStart, ta.selectionEnd];
  ta.setRangeText(text, a, b, 'end');
  ta.focus();
  ta.dispatchEvent(new Event('input'));
}

function update() {
  const e = S.file.entries[S.index];
  const text = field('msgstr').value;
  field('preview').replaceChildren(renderPreview(text));
  // characters per line against the reference's longest line
  const limit = e.ref ? Math.max(...clean(e.ref).split('\n').map((l) => l.length)) : 0;
  field('widths').replaceChildren(...(text ? clean(text).split('\n') : []).map((line) => {
    const w = width(line);
    return el('li', { className: limit && w > Math.max(limit * 1.15, limit + 4) ? 'over' : '', textContent: limit ? `${w}/${limit}` : `${w}` });
  }));
  // which of the source's codes the draft has
  const want = codesOf(e.msgid), got = codesOf(text);
  for (const b of $$('button', field('chips'))) {
    const n = want.filter((c) => c === b.dataset.code).length, m = got.filter((c) => c === b.dataset.code).length;
    b.className = m === n ? 'used' : m > n ? 'over' : '';
    b.title = `Insert ${b.dataset.code} (the source has ${n}, the Thai ${m})`;
  }
  $('[data-act="approve"]').hidden = !text || status() === 'done' && S.saved.status === 'done';
  field('status').classList.toggle('blank', !text);
  const state = field('state');
  if (dirty()) { state.textContent = 'Not saved'; state.className = 'state'; }
}

function dirty() {
  if (!S.file || S.index < 0 || !field('msgstr')) return false;
  return field('msgstr').value !== S.saved.msgstr || (field('msgstr').value && status() !== S.saved.status)
    || field('note').value.trim() !== S.saved.notes.trim();
}

let checkTimer = 0;
function queueCheck() {
  clearTimeout(checkTimer);
  const e = S.file.entries[S.index], path = S.file.path;
  checkTimer = setTimeout(async () => {
    const text = field('msgstr')?.value ?? '';
    try {
      const checks = await api('/api/check', { path, ctx: e.ctx, msgstr: text });
      if (S.file.entries[S.index] === e) showChecks(checks, text);
    } catch { /* the save reports it */ }
  }, 300);
}

function showChecks(checks, text) {
  const ul = field('checks');
  if (!ul) return;
  if (!text) { ul.replaceChildren(); return; }
  if (!checks.length) {
    ul.replaceChildren(el('li', { className: 'ok', textContent: 'Control codes match the source.' }));
    return;
  }
  ul.replaceChildren(...checks.map((c) => el('li', { className: c.level, textContent: c.text })));
}

async function save(next) {
  if (!S.file || S.index < 0) return true;
  const e = S.file.entries[S.index];
  const state = field('state');
  const body = { path: S.file.path, ctx: e.ctx, msgstr: field('msgstr').value, status: status(),
    note: field('note').value };
  if (dirty()) {
    state.textContent = 'Saving…';
    try {
      const r = await api('/api/save', body);
      Object.assign(e, r.entry);
      const counts = { done: r.done, fuzzy: r.fuzzy, draft: r.draft, total: r.total };
      Object.assign(S.file, counts, { voiced: r.voiced });
      Object.assign(S.files.find((x) => x.path === S.file.path), counts);
      S.saved = savedOf(e);
      state.textContent = `Saved ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
      state.className = 'state saved';
      showChecks(r.entry.checks, e.msgstr);
      renderFiles();
      renderMessages();
    } catch (err) {
      state.textContent = `Not saved: ${err.message}`;
      state.className = 'state failed';
      return false;
    }
  }
  if (next) {
    const n = S.file.entries.findIndex((x, j) => j > S.index && (!x.msgstr || x.state !== 'done'));
    if (n >= 0) await select(n, true);
    else if (S.index + 1 < S.file.entries.length) await select(S.index + 1, true);
  }
  return true;
}

function approve() {
  if (!field('msgstr').value) return;
  setStatus('done');
  save(true);
}

// Leaving a message saves it when it changed; false when the save failed (the page stays).
async function leave() {
  if (!dirty()) return true;
  return save(false);
}

function move(step) {
  if (!S.file) return;
  const list = S.file.entries;
  let i = S.index + step;
  while (i >= 0 && i < list.length && !entryVisible(list[i])) i += step;
  if (i >= 0 && i < list.length) select(i);
}

// ---- voices

function stopVoice() {
  S.audio.pause();
  if (S.playing) S.playing.classList.remove('playing');
  $$('.voice-bar i').forEach((i) => { i.style.width = '0'; });
  S.playing = null;
}

function toggleVoice(b) {
  if (b.hidden) return;
  if (S.playing === b) { stopVoice(); return; }
  stopVoice();
  const e = S.file.entries[S.index];
  S.audio.src = `/api/voice?path=${encodeURIComponent(S.file.path)}&ctx=${encodeURIComponent(e.ctx)}&lang=${b.dataset.lang}`;
  S.playing = b;
  b.classList.add('playing');
  S.audio.play().catch((err) => {
    stopVoice();
    $('.voice-label', b).textContent = `Cannot play: ${err.message}`;
  });
}

S.audio.addEventListener('timeupdate', () => {
  if (!S.playing || !S.audio.duration) return;
  $('.voice-bar i', S.playing).style.width = `${(100 * S.audio.currentTime) / S.audio.duration}%`;
});
S.audio.addEventListener('ended', stopVoice);

// ---- glossary

function renderGlossary(e) {
  const plain = clean(e.plain ?? e.msgid);
  const hits = S.glossary.filter((g) => g.source && g.source.length > 1 && plain.includes(g.source))
    .sort((a, b) => b.source.length - a.source.length)
    .filter((g, i, all) => !all.slice(0, i).some((h) => h.source.includes(g.source)))
    .slice(0, 12);
  const pane = field('glossary-pane');
  pane.hidden = !hits.length;
  field('glossary').replaceChildren(...hits.map((g) => {
    const input = el('input', { value: g.translation || '', lang: 'th', placeholder: 'Thai name', ariaLabel: `Thai for ${g.source}` });
    const commit = async () => {
      if (input.value === (g.translation || '')) return;
      try {
        await api('/api/glossary', { source: g.source, translation: input.value });
        g.translation = input.value;
        input.classList.add('saved');
        input.title = 'Saved to glossary.tsv';
      } catch (err) { input.title = `Not saved: ${err.message}`; }
    };
    input.addEventListener('change', commit);
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
      if (ev.key === 'Enter' && ev.altKey && input.value) insert(input.value);
    });
    input.title = 'Enter saves the name; Alt+Enter also inserts it into the Thai';
    return el('li', {}, el('span', { className: 'ja', lang: 'ja', textContent: g.source }),
      el('span', { className: 'en', textContent: g.reference || '' }), input);
  }));
}

// ---- search

let searchTimer = 0;
function endSearch() {
  if (!S.searching) return;
  S.searching = false;
  $('#search').value = '';
}

async function runSearch(q) {
  if (!q.trim()) { S.searching = false; renderMessages(); return; }
  S.searching = true;
  const results = await api(`/api/search?q=${encodeURIComponent(q)}`).catch(() => []);
  if (!S.searching) return;
  $('#file-name').textContent = `Search: ${q}`;
  $('#file-count').textContent = results.length >= 200 ? 'The first 200 messages found' : `${results.length} messages found`;
  $('#message-list').replaceChildren(...results.map((r) => {
    const b = el('button', { type: 'button' }, el('span', { className: 'mark' }),
      el('span', { className: 'label', textContent: `${r.path} · ${r.ctx.split('_').slice(-2).join('_')}` }),
      el('span', { className: 'line', lang: 'ja', textContent: clean(r.msgid) }));
    const hit = r.where.includes('translation') ? clean(r.msgstr) : r.where.includes('reference') ? clean(r.ref) : '';
    if (hit) b.append(el('span'), el('span', { className: 'hit', textContent: hit }));
    b.addEventListener('click', () => openFile(r.path, r.ctx));
    return el('li', { className: r.msgstr ? 'done' : '' }, b);
  }));
}

// ---- keys and start

document.addEventListener('keydown', (ev) => {
  const ed = $('#editor');
  if (!S.file || S.index < 0 || !field('msgstr')) return;
  if (ev.ctrlKey && ev.key === 'Enter') { ev.preventDefault(); save(true); }
  else if (ev.ctrlKey && ev.key.toLowerCase() === 's') { ev.preventDefault(); save(false); }
  else if (ev.altKey && ev.key === 'ArrowUp') { ev.preventDefault(); move(-1); }
  else if (ev.altKey && ev.key === 'ArrowDown') { ev.preventDefault(); move(1); }
  else if (ev.altKey && ev.key.toLowerCase() === 'j') { ev.preventDefault(); toggleVoice($('[data-voice="slot"]', ed)); }
  else if (ev.altKey && ev.key.toLowerCase() === 'e') { ev.preventDefault(); toggleVoice($('[data-voice="reference"]', ed)); }
  else if (ev.altKey && ev.key.toLowerCase() === 'a') { ev.preventDefault(); approve(); }
  else if (ev.altKey && ev.key.toLowerCase() === 'd') { ev.preventDefault(); setStatus('draft'); }
  else if (ev.altKey && ev.key.toLowerCase() === 'r') {
    ev.preventDefault();
    setStatus(status() === 'fuzzy' ? (S.saved.status === 'fuzzy' ? 'done' : S.saved.status) : 'fuzzy');
  }
});

window.addEventListener('beforeunload', (ev) => { if (dirty()) { ev.preventDefault(); ev.returnValue = ''; } });

function setupFilters() {
  for (const [sel, key, after] of [['.files .filters', 'fileFilter', renderFiles], ['.messages .filters', 'show', renderMessages]]) {
    for (const b of $$(`${sel} button`)) {
      b.addEventListener('click', () => {
        $$(`${sel} button`).forEach((x) => { x.classList.remove('on'); x.ariaChecked = 'false'; });
        b.classList.add('on');
        b.ariaChecked = 'true';
        S[key] = b.dataset.filter || b.dataset.show;
        after();
      });
    }
  }
  $('#search').addEventListener('input', (ev) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(ev.target.value), 300);
  });
}

async function start() {
  setupFilters();
  try {
    S.project = await api('/api/project');
  } catch (err) {
    $('#editor').replaceChildren(el('div', { className: 'empty' }, el('p', { textContent: `The project did not load: ${err.message}` })));
    return;
  }
  S.files = S.project.files;
  $('#subtitle').textContent = `${LANG_NAMES[S.project.slot] || S.project.slot} → ${LANG_NAMES[S.project.language] || S.project.language}`
    + (S.project.reference ? `, beside ${LANG_NAMES[S.project.reference] || S.project.reference}` : '');
  document.title = `${S.project.mod} · Translator's desk`;
  if (S.project.fonts.dialogue) {
    const face = new FontFace('Desk Thai', `url(/font/${encodeURIComponent(S.project.fonts.dialogue)})`);
    face.load().then((f) => document.fonts.add(f)).catch(() => {});
  }
  S.glossary = await api('/api/glossary').catch(() => []);
  renderFiles();
  const [path, ctx] = location.hash ? location.hash.slice(1).split('::').map(decodeURIComponent)
    : JSON.parse(localStorage.deskLast || '[]');
  if (path && S.files.some((f) => f.path === path)) openFile(path, ctx);
}

start();
