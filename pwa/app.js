/* Alonarg phone companion — offline-first PWA.
 * Pure vanilla JS, no build step, no dependencies.
 * Records meetings, queues them in IndexedDB, syncs to the PC "engine",
 * and views the shared dashboard.
 */
'use strict';

/* =====================================================================
 * Config (backend URL + token in localStorage)
 * ===================================================================== */
const LS_BASE = 'alonarg.base';
const LS_TOKEN = 'alonarg.token';

function normalizeBase(s) {
  let b = (s || '').trim().replace(/\/+$/, ''); // trim spaces + trailing slash(es)
  if (b && !/^https?:\/\//i.test(b)) b = 'https://' + b; // forgive a missing scheme
  return b;
}

function getConfig() {
  const base = normalizeBase(localStorage.getItem(LS_BASE));
  const token = (localStorage.getItem(LS_TOKEN) || '').trim();
  return { base, token, configured: !!base };
}

function setConfig(base, token) {
  localStorage.setItem(LS_BASE, normalizeBase(base));
  localStorage.setItem(LS_TOKEN, (token || '').trim());
}

/* Centralised backend access. Throws if no backend configured. */
async function apiFetch(path, opts = {}) {
  const { base, token } = getConfig();
  if (!base) throw new Error('No backend configured');
  const headers = new Headers(opts.headers || {});
  if (token) headers.set('Authorization', 'Bearer ' + token);
  const url = base + (path.startsWith('/') ? path : '/' + path);
  return fetch(url, { ...opts, headers });
}

function audioUrl(id) {
  const { base, token } = getConfig();
  if (!base) return '';
  const q = token ? '?token=' + encodeURIComponent(token) : '';
  return base + '/audio/' + encodeURIComponent(id) + q;
}

/* =====================================================================
 * IndexedDB — local recording queue
 * ===================================================================== */
const DB_NAME = 'alonarg';
const DB_VERSION = 1;
const STORE = 'recordings';
let _dbPromise = null;

function openDB() {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return _dbPromise;
}

function idbTx(mode, fn) {
  return openDB().then(
    (db) =>
      new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, mode);
        const store = tx.objectStore(STORE);
        let result;
        Promise.resolve(fn(store)).then((r) => {
          result = r;
        });
        tx.oncomplete = () => resolve(result);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
      })
  );
}

function dbPut(rec) {
  return idbTx('readwrite', (store) => store.put(rec));
}

function dbGetAll() {
  return idbTx('readonly', (store) => {
    return new Promise((resolve, reject) => {
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  });
}

function dbGet(id) {
  return idbTx('readonly', (store) => {
    return new Promise((resolve, reject) => {
      const req = store.get(id);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
  });
}

function dbDelete(id) {
  return idbTx('readwrite', (store) => store.delete(id));
}

/* =====================================================================
 * Helpers
 * ===================================================================== */
function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

function fmtClock(totalS) {
  const s = Math.max(0, Math.floor(totalS));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return (h > 0 ? h + ':' + pad2(m) : pad2(m)) + ':' + pad2(sec);
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function fmtDuration(s) {
  if (!s && s !== 0) return '';
  return fmtClock(s);
}

function escapeHtml(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function extForMime(mime) {
  if (!mime) return 'webm';
  if (mime.indexOf('mp4') !== -1) return 'm4a';
  if (mime.indexOf('webm') !== -1) return 'webm';
  if (mime.indexOf('ogg') !== -1) return 'ogg';
  if (mime.indexOf('wav') !== -1) return 'wav';
  return 'webm';
}

function $(sel) {
  return document.querySelector(sel);
}

/* =====================================================================
 * Recording (MediaRecorder)
 * ===================================================================== */
const MIME_CANDIDATES = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm'];

function pickMimeType() {
  if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) {
    return null;
  }
  for (const m of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return ''; // MediaRecorder exists but reports nothing; let it pick a default
}

const recState = {
  recorder: null,
  stream: null,
  chunks: [],
  mime: '',
  startedAt: 0,
  timerId: null,
  recording: false,
};

async function startRecording() {
  const errEl = $('#rec-error');
  errEl.hidden = true;

  if (typeof MediaRecorder === 'undefined') {
    showRecError(
      'Recording is not supported on this browser. On iPhone, use Safari 14.3 or newer.'
    );
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    if (err && (err.name === 'NotAllowedError' || err.name === 'SecurityError')) {
      showRecError('Microphone permission was denied. Allow mic access and try again.');
    } else if (err && err.name === 'NotFoundError') {
      showRecError('No microphone was found on this device.');
    } else {
      showRecError('Could not access the microphone: ' + (err && err.message ? err.message : err));
    }
    return;
  }

  const mime = pickMimeType();
  if (mime === null) {
    stream.getTracks().forEach((t) => t.stop());
    showRecError('Audio recording is not supported on this browser.');
    return;
  }

  let recorder;
  try {
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
  } catch (err) {
    stream.getTracks().forEach((t) => t.stop());
    showRecError('Could not start the recorder: ' + (err && err.message ? err.message : err));
    return;
  }

  recState.recorder = recorder;
  recState.stream = stream;
  recState.chunks = [];
  recState.mime = recorder.mimeType || mime || 'audio/webm';
  recState.startedAt = Date.now();
  recState.recording = true;

  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) recState.chunks.push(e.data);
  };
  recorder.onstop = onRecorderStop;
  recorder.onerror = () => {
    showRecError('A recording error occurred. The recording was stopped.');
  };

  recorder.start(1000); // gather data in 1s timeslices (robust on iOS)

  // UI
  const btn = $('#rec-btn');
  btn.dataset.state = 'recording';
  btn.setAttribute('aria-label', 'Stop recording');
  $('#rec-hint').textContent = 'Recording… tap to stop';
  tickTimer();
  recState.timerId = setInterval(tickTimer, 250);
}

function stopRecording() {
  if (recState.recorder && recState.recording) {
    recState.recording = false;
    try {
      recState.recorder.stop();
    } catch (_) {
      /* ignore */
    }
  }
}

async function onRecorderStop() {
  if (recState.timerId) {
    clearInterval(recState.timerId);
    recState.timerId = null;
  }
  if (recState.stream) {
    recState.stream.getTracks().forEach((t) => t.stop());
  }

  const durationS = Math.max(1, Math.round((Date.now() - recState.startedAt) / 1000));
  const mime = recState.mime;
  const blob = new Blob(recState.chunks, { type: mime });
  recState.chunks = [];

  // reset record UI
  const btn = $('#rec-btn');
  btn.dataset.state = 'idle';
  btn.setAttribute('aria-label', 'Start recording');
  $('#rec-timer').textContent = '00:00';
  $('#rec-hint').textContent = 'Tap to record this meeting';

  if (blob.size === 0) {
    showRecError('Nothing was recorded. Please try again.');
    return;
  }

  const now = new Date();
  const rec = {
    id: uuid(),
    title: 'Phone meeting ' + now.toLocaleString(),
    createdAt: now.toISOString(),
    mime,
    blob,
    durationS,
    status: 'local',
    serverId: null,
    error: null,
  };

  try {
    await dbPut(rec);
    toast('Recording saved — ' + fmtClock(durationS));
  } catch (err) {
    showRecError('Could not save the recording locally: ' + (err && err.message ? err.message : err));
    return;
  }

  await refreshRecordings();
  // Try to sync immediately if a backend is configured.
  trySync();
}

function tickTimer() {
  const s = (Date.now() - recState.startedAt) / 1000;
  $('#rec-timer').textContent = fmtClock(s);
}

function showRecError(msg) {
  const el = $('#rec-error');
  el.textContent = msg;
  el.hidden = false;
}

let _toastTimer = null;
function toast(msg) {
  const el = $('#rec-saved');
  el.textContent = msg;
  el.hidden = false;
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 2600);
}

/* =====================================================================
 * Sync — upload local recordings to the engine
 * ===================================================================== */
let _syncing = false;

async function trySync(manual = false) {
  const { configured } = getConfig();
  if (!configured) {
    if (manual) setSyncStatus('No backend configured. Add it in Settings.', true);
    return;
  }
  if (_syncing) return;

  const all = await dbGetAll();
  const pending = all.filter((r) => r.status === 'local' || r.status === 'error');
  if (pending.length === 0) {
    if (manual) setSyncStatus('Nothing to sync — all recordings uploaded.');
    return;
  }

  _syncing = true;
  setSyncStatus('Syncing ' + pending.length + ' recording' + (pending.length > 1 ? 's' : '') + '…');

  let ok = 0;
  let failed = 0;
  for (const rec of pending) {
    const success = await uploadOne(rec);
    if (success) ok++;
    else failed++;
  }

  _syncing = false;
  if (failed === 0) {
    setSyncStatus(ok > 0 ? 'Synced ' + ok + ' recording' + (ok > 1 ? 's' : '') + '.' : '');
  } else {
    setSyncStatus(
      failed + ' upload' + (failed > 1 ? 's' : '') + ' failed — will retry. Check connection.',
      true
    );
  }
  await refreshRecordings();
}

async function uploadOne(rec) {
  // mark uploading
  rec.status = 'uploading';
  rec.error = null;
  await dbPut(rec);
  updateLocalCard(rec, 5);

  try {
    const fd = new FormData();
    const filename = 'recording.' + extForMime(rec.mime);
    fd.append('file', rec.blob, filename);
    fd.append('title', rec.title);
    fd.append('source', 'phone');
    fd.append('created_at', rec.createdAt);
    fd.append('client_duration_s', String(rec.durationS));

    const resp = await apiFetch('/api/upload', { method: 'POST', body: fd });
    if (!resp.ok) {
      throw new Error('Server responded ' + resp.status);
    }
    const data = await resp.json().catch(() => ({}));

    rec.status = 'uploaded';
    rec.serverId = data && (data.id != null) ? data.id : rec.serverId;
    rec.error = null;
    // Keep the blob so the user can still play it offline.
    await dbPut(rec);
    updateLocalCard(rec, 100);
    return true;
  } catch (err) {
    rec.status = 'error';
    rec.error = err && err.message ? err.message : String(err);
    await dbPut(rec);
    updateLocalCard(rec, 0);
    return false;
  }
}

function setSyncStatus(msg, isError) {
  const el = $('#sync-status');
  if (!msg) {
    el.hidden = true;
    el.textContent = '';
    return;
  }
  el.hidden = false;
  el.textContent = msg;
  el.style.color = isError ? 'var(--danger)' : 'var(--text-muted)';
}

/* Update the progress bar on an in-flight local card without a full re-render. */
function updateLocalCard(rec, pct) {
  const card = document.querySelector('[data-local-id="' + cssEscape(rec.id) + '"]');
  if (!card) return;
  const bar = card.querySelector('.card-progress > span');
  if (bar) bar.style.width = Math.max(0, Math.min(100, pct)) + '%';
  const badge = card.querySelector('.status-badge');
  if (badge) {
    badge.className = 'status-badge status-' + rec.status;
    badge.textContent = badgeLabel(rec.status);
  }
}

function cssEscape(s) {
  if (window.CSS && CSS.escape) return CSS.escape(s);
  return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
}

/* =====================================================================
 * Connection status (header pill)
 * ===================================================================== */
let _connTimer = null;

async function checkConnection() {
  const pill = $('#conn-pill');
  const label = $('#conn-label');
  const { configured } = getConfig();
  if (!configured) {
    pill.dataset.state = 'unconfigured';
    label.textContent = 'Not set';
    return false;
  }
  pill.dataset.state = 'checking';
  label.textContent = 'Checking…';
  try {
    const resp = await apiFetch('/api/status', { method: 'GET' });
    if (resp.ok) {
      pill.dataset.state = 'connected';
      label.textContent = 'Connected';
      return true;
    }
    throw new Error('status ' + resp.status);
  } catch (_) {
    pill.dataset.state = 'offline';
    label.textContent = 'Offline';
    return false;
  }
}

/* =====================================================================
 * Recordings list (merge local + remote)
 * ===================================================================== */
let _remoteCache = [];
let _pollTimer = null;

function badgeLabel(status) {
  switch (status) {
    case 'local':
      return 'Pending sync';
    case 'uploading':
      return 'Uploading';
    case 'uploaded':
      return 'Uploaded';
    case 'transcribing':
      return 'Transcribing';
    case 'summarizing':
      return 'Summarizing';
    case 'done':
      return 'Done';
    case 'error':
      return 'Error';
    case 'recording':
      return 'Recording';
    default:
      return status || 'Unknown';
  }
}

async function fetchRemote() {
  const { configured } = getConfig();
  if (!configured) return [];
  try {
    const resp = await apiFetch('/api/recordings', { method: 'GET' });
    if (!resp.ok) return _remoteCache;
    const data = await resp.json();
    _remoteCache = Array.isArray(data) ? data : [];
    return _remoteCache;
  } catch (_) {
    return _remoteCache; // tolerate offline backend
  }
}

async function refreshRecordings() {
  const [local, remote] = await Promise.all([dbGetAll(), fetchRemote()]);

  // Build a set of server ids already represented remotely, so an "uploaded"
  // local item that now appears remotely isn't shown twice.
  const remoteIds = new Set(remote.map((r) => String(r.id)));

  const items = [];

  for (const r of local) {
    if (r.status === 'uploaded' && r.serverId != null && remoteIds.has(String(r.serverId))) {
      continue; // superseded by the remote copy
    }
    items.push({
      kind: 'local',
      id: r.id,
      serverId: r.serverId,
      title: r.title,
      createdAt: r.createdAt,
      durationS: r.durationS,
      status: r.status,
      error: r.error,
      summary: null,
      actionCount: 0,
      sortKey: r.createdAt || '',
    });
  }

  for (const r of remote) {
    const summary = r.summary || null;
    const actionItems = summary && Array.isArray(summary.action_items) ? summary.action_items : [];
    items.push({
      kind: 'remote',
      id: r.id,
      title: (summary && summary.title) || r.title || 'Untitled meeting',
      createdAt: r.created_at,
      durationS: r.duration_s,
      status: r.status,
      summary: summary && summary.summary ? summary.summary : null,
      actionCount: actionItems.length,
      sortKey: r.created_at || '',
    });
  }

  items.sort((a, b) => String(b.sortKey).localeCompare(String(a.sortKey)));

  renderCards(items);
  managePolling(remote);
  return items;
}

function renderCards(items) {
  const list = $('#cards');
  const empty = $('#cards-empty');
  if (items.length === 0) {
    list.innerHTML = '';
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  list.innerHTML = items.map(cardHtml).join('');
}

function cardHtml(it) {
  const tappable = it.kind === 'remote' && it.status === 'done';
  const metaParts = [];
  if (it.createdAt) metaParts.push(escapeHtml(fmtDate(it.createdAt)));
  if (it.durationS) metaParts.push(escapeHtml(fmtDuration(it.durationS)));
  const meta = metaParts.join('<span class="dot-sep">&middot;</span>');

  const dataAttr =
    it.kind === 'local'
      ? 'data-local-id="' + escapeHtml(it.id) + '"'
      : 'data-remote-id="' + escapeHtml(it.id) + '"';

  let body = '';
  if (it.summary) {
    body += '<p class="card-summary">' + escapeHtml(it.summary) + '</p>';
  }

  let footer = '';
  if (it.actionCount > 0) {
    footer +=
      '<span class="action-count">' +
      it.actionCount +
      ' action item' +
      (it.actionCount > 1 ? 's' : '') +
      '</span>';
  }

  let extra = '';
  if (it.kind === 'local' && (it.status === 'uploading')) {
    extra =
      '<div class="card-progress"><span style="width:5%"></span></div>';
  }
  if (it.kind === 'local' && it.status === 'error') {
    extra =
      '<button class="btn retry-btn" data-retry="' +
      escapeHtml(it.id) +
      '" type="button">Retry upload</button>';
    if (it.error) {
      footer +=
        '<span class="action-count" style="color:var(--danger)">' +
        escapeHtml(it.error) +
        '</span>';
    }
  }

  return (
    '<li class="card' +
    (tappable ? ' tappable' : '') +
    '" ' +
    dataAttr +
    (tappable ? ' data-open="' + escapeHtml(it.id) + '"' : '') +
    '>' +
    '<div class="card-top">' +
    '<div style="min-width:0">' +
    '<h3 class="card-title">' +
    escapeHtml(it.title) +
    '</h3>' +
    (meta ? '<div class="card-meta">' + meta + '</div>' : '') +
    '</div>' +
    '<span class="status-badge status-' +
    escapeHtml(it.status) +
    '">' +
    escapeHtml(badgeLabel(it.status)) +
    '</span>' +
    '</div>' +
    body +
    (footer ? '<div class="card-footer">' + footer + '</div>' : '') +
    extra +
    '</li>'
  );
}

/* Poll while any remote item is still processing. */
function managePolling(remote) {
  const active = remote.some(
    (r) => r.status === 'transcribing' || r.status === 'summarizing' || r.status === 'recording'
  );
  if (active && !_pollTimer) {
    _pollTimer = setInterval(() => {
      // Only poll when the recordings view is visible to save battery.
      if (currentTab === 'recordings' || true) refreshRecordings();
    }, 4000);
  } else if (!active && _pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
}

/* =====================================================================
 * Detail view
 * ===================================================================== */
async function openDetail(remoteId) {
  showView('detail');
  const body = $('#detail-body');
  body.innerHTML = '<div class="detail-loading">Loading…</div>';

  let rec;
  try {
    const resp = await apiFetch('/api/recordings/' + encodeURIComponent(remoteId), { method: 'GET' });
    if (!resp.ok) throw new Error('Server responded ' + resp.status);
    rec = await resp.json();
  } catch (err) {
    body.innerHTML =
      '<div class="panel">Could not load this recording.<br><span class="muted">' +
      escapeHtml(err && err.message ? err.message : String(err)) +
      '</span></div>';
    return;
  }

  const summary = rec.summary || null;
  const transcript = rec.transcript || null;
  const title = (summary && summary.title) || rec.title || 'Untitled meeting';

  const metaParts = [];
  if (rec.created_at) metaParts.push(escapeHtml(fmtDate(rec.created_at)));
  if (rec.duration_s) metaParts.push(escapeHtml(fmtDuration(rec.duration_s)));
  const meta = metaParts.join('<span class="dot-sep">&middot;</span>');

  let html = '';
  html += '<h2 class="detail-title">' + escapeHtml(title) + '</h2>';
  if (meta) html += '<div class="detail-meta">' + meta + '</div>';

  html +=
    '<div class="player"><audio controls preload="none" src="' +
    escapeHtml(audioUrl(rec.id)) +
    '"></audio></div>';

  if (summary && summary.summary) {
    html +=
      '<div class="panel"><h3>Summary</h3><p class="summary-text">' +
      escapeHtml(summary.summary) +
      '</p></div>';
  }

  html += listPanel('Action Items', summary && summary.action_items);
  html += listPanel('Next Steps', summary && summary.next_steps);

  if (transcript && transcript.text) {
    html +=
      '<details class="panel transcript"><summary>Full transcript</summary>' +
      '<div class="transcript-text">' +
      escapeHtml(transcript.text) +
      '</div></details>';
  }

  body.innerHTML = html;
}

function listPanel(heading, arr) {
  if (!Array.isArray(arr) || arr.length === 0) return '';
  const items = arr.map((x) => '<li>' + escapeHtml(x) + '</li>').join('');
  return (
    '<div class="panel"><h3>' +
    escapeHtml(heading) +
    '</h3><ul class="bullet-list">' +
    items +
    '</ul></div>'
  );
}

/* =====================================================================
 * Calendar (agenda + auto-record toggles)
 * ===================================================================== */
let _calEventsByKey = {};

async function loadCalendar() {
  const list = $('#cal-list');
  if (!list) return;
  const { configured } = getConfig();
  if (!configured) {
    list.innerHTML = '<div class="empty-state">Set your laptop\'s backend URL in Settings to see your calendar.</div>';
    return;
  }
  list.innerHTML = '<p class="muted">Loading…</p>';
  let data;
  try {
    const resp = await apiFetch('/api/calendar/events?days=7');
    if (!resp.ok) throw new Error('status ' + resp.status);
    data = await resp.json();
  } catch (_) {
    list.innerHTML = '<div class="empty-state">Couldn\'t reach your laptop. Check the connection.</div>';
    return;
  }
  if (!data || !data.connected) {
    list.innerHTML = '<div class="empty-state">Calendar not connected. Connect it from the desktop app (Settings &rarr; Calendar).</div>';
    return;
  }
  renderCalendarAgenda(data.events || []);
}

function renderCalendarAgenda(events) {
  const list = $('#cal-list');
  _calEventsByKey = {};
  if (!events.length) {
    list.innerHTML = '<div class="empty-state">No meetings in the next 7 days.</div>';
    return;
  }
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const groups = [];
  const byKey = {};
  events.forEach((ev) => {
    _calEventsByKey[ev.key] = ev;
    const s = new Date(ev.start);
    if (isNaN(s.getTime())) return;
    const dayStart = new Date(s); dayStart.setHours(0, 0, 0, 0);
    const dk = dayStart.toISOString().slice(0, 10);
    if (!byKey[dk]) { byKey[dk] = { date: dayStart, items: [] }; groups.push(byKey[dk]); }
    byKey[dk].items.push(ev);
  });
  groups.sort((a, b) => a.date - b.date);

  let html = '';
  for (const g of groups) {
    const isToday = g.date.getTime() === today.getTime();
    const label = (isToday ? 'Today · ' : '') +
      g.date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    html += '<div class="cal-day"><div class="cal-day-head">' + escapeHtml(label) + '</div>';
    for (const ev of g.items) {
      const time = fmtTimeRange(ev);
      html +=
        '<div class="cal-ev' + (ev.auto_record ? ' armed' : '') + '" data-key="' + escapeHtml(ev.key) + '">' +
        '<div class="cal-ev-info">' +
        '<div class="cal-ev-time">' + escapeHtml(time) + '</div>' +
        '<div class="cal-ev-title">' + escapeHtml(ev.subject || '(no title)') + '</div>' +
        '</div>' +
        '<label class="switch" title="Auto-record"><input type="checkbox" data-key="' + escapeHtml(ev.key) + '"' +
        (ev.auto_record ? ' checked' : '') + '><span class="slider"></span></label>' +
        '</div>';
    }
    html += '</div>';
  }
  list.innerHTML = html;
}

function fmtTimeRange(ev) {
  const opt = { hour: 'numeric', minute: '2-digit' };
  const s = new Date(ev.start), e = new Date(ev.end);
  const ss = isNaN(s.getTime()) ? '' : s.toLocaleTimeString(undefined, opt);
  const ee = isNaN(e.getTime()) ? '' : e.toLocaleTimeString(undefined, opt);
  return ss + (ee ? ' – ' + ee : '');
}

async function onAutoRecordToggle(cb) {
  const ev = _calEventsByKey[cb.getAttribute('data-key')];
  if (!ev) return;
  const enabled = cb.checked;
  cb.disabled = true;
  try {
    const resp = await apiFetch('/api/calendar/autorecord', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event: ev, enabled: enabled }),
    });
    if (!resp.ok) throw new Error('status ' + resp.status);
    ev.auto_record = enabled;
    const row = cb.closest('.cal-ev');
    if (row) row.classList.toggle('armed', enabled);
    toast(enabled ? 'Will auto-record this meeting' : 'Auto-record off');
  } catch (_) {
    cb.checked = !enabled;
    toast('Could not update — check the connection.');
  }
  cb.disabled = false;
}

/* =====================================================================
 * Ask (local AI across all meetings)
 * ===================================================================== */
async function doAsk() {
  const input = $('#ask-input');
  const out = $('#ask-answer');
  const btn = $('#ask-btn');
  const q = (input.value || '').trim();
  if (!q) return;
  const { configured } = getConfig();
  out.hidden = false;
  out.className = 'ask-answer';
  if (!configured) {
    out.className = 'ask-answer fail';
    out.textContent = 'Set your laptop\'s backend URL in Settings first.';
    return;
  }
  out.textContent = 'Thinking…';
  btn.disabled = true;
  try {
    const resp = await apiFetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      out.textContent = data.answer || '(no answer)';
    } else {
      out.className = 'ask-answer fail';
      out.textContent = data.detail || 'Could not reach the local AI. Make sure your laptop and Ollama are running.';
    }
  } catch (_) {
    out.className = 'ask-answer fail';
    out.textContent = 'Could not reach your laptop. Check the connection.';
  }
  btn.disabled = false;
}

/* =====================================================================
 * View / tab navigation
 * ===================================================================== */
let currentTab = 'record';

function showView(view) {
  const views = ['record', 'recordings', 'detail', 'calendar', 'ask', 'settings'];
  for (const v of views) {
    const el = document.getElementById('view-' + v);
    if (el) el.hidden = v !== view;
  }
  // Tab highlight: detail belongs under "recordings".
  const tabFor = view === 'detail' ? 'recordings' : view;
  document.querySelectorAll('.tab').forEach((t) => {
    t.setAttribute('aria-selected', t.dataset.tab === tabFor ? 'true' : 'false');
  });
  if (view !== 'detail') currentTab = view;
}

function switchTab(tab) {
  showView(tab);
  if (tab === 'recordings') {
    refreshRecordings();
    trySync();
  } else if (tab === 'settings') {
    loadSettingsForm();
  } else if (tab === 'record') {
    pollLaptopStatus();
  } else if (tab === 'calendar') {
    loadCalendar();
  } else if (tab === 'ask') {
    const i = $('#ask-input');
    if (i) i.focus();
  }
}

/* =====================================================================
 * Settings
 * ===================================================================== */
function loadSettingsForm() {
  const { base, token } = getConfig();
  $('#cfg-base').value = base;
  $('#cfg-token').value = token;
  $('#test-result').hidden = true;
}

async function onTestConnection() {
  const base = $('#cfg-base').value;
  const token = $('#cfg-token').value;
  // Use the entered (not necessarily saved) values for the test.
  const cleanBase = normalizeBase(base);
  const result = $('#test-result');
  result.hidden = false;
  result.className = 'test-result';
  result.textContent = 'Testing…';

  if (!cleanBase) {
    result.className = 'test-result fail';
    result.textContent = 'Enter a backend URL first.';
    return;
  }

  try {
    const headers = new Headers();
    if (token && token.trim()) headers.set('Authorization', 'Bearer ' + token.trim());
    const resp = await fetch(cleanBase + '/api/status', { method: 'GET', headers });
    if (resp.ok) {
      result.className = 'test-result ok';
      result.textContent = 'Connected — the engine responded.';
    } else {
      result.className = 'test-result fail';
      result.textContent = 'Reached the server but got HTTP ' + resp.status + '.';
    }
  } catch (err) {
    result.className = 'test-result fail';
    result.textContent =
      'Could not reach the backend. Check the URL and that your PC engine is running.';
  }
}

function onSaveSettings(e) {
  e.preventDefault();
  setConfig($('#cfg-base').value, $('#cfg-token').value);
  // reflect normalised value back in the field
  $('#cfg-base').value = getConfig().base;
  toast('Settings saved');
  checkConnection().then(() => {
    refreshRecordings();
    trySync();
  });
}

/* =====================================================================
 * Record on laptop (trigger the PC engine) + meeting nudges (web push)
 * ===================================================================== */
let _laptopRecording = false;

async function pollLaptopStatus() {
  const wrap = $('#laptop-rec');
  const btn = $('#laptop-rec-btn');
  if (!wrap || !btn) return;
  const { configured } = getConfig();
  wrap.hidden = !configured;
  if (!configured) return;
  try {
    const resp = await apiFetch('/api/status');
    if (!resp.ok) return;
    const s = await resp.json();
    _laptopRecording = !!s.recording;
    btn.dataset.state = _laptopRecording ? 'recording' : 'idle';
    btn.textContent = _laptopRecording ? 'Stop laptop recording' : 'Record on laptop';
  } catch (_) {
    /* laptop offline — leave button as-is */
  }
}

async function toggleLaptopRecording() {
  const btn = $('#laptop-rec-btn');
  const wasRecording = _laptopRecording;
  btn.disabled = true;
  try {
    const path = wasRecording ? '/api/record/stop' : '/api/record/start';
    const resp = await apiFetch(path, { method: 'POST' });
    if (resp.ok) {
      toast(wasRecording ? 'Laptop recording stopped — processing.' : 'Recording on your laptop…');
      await pollLaptopStatus();
      if (wasRecording) refreshRecordings();
    } else {
      toast('Could not reach your laptop (HTTP ' + resp.status + ').');
    }
  } catch (_) {
    toast('Could not reach your laptop. Is it awake and connected?');
  }
  btn.disabled = false;
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

async function enableNudges() {
  const result = $('#nudge-result');
  result.hidden = false;
  result.className = 'test-result';
  result.textContent = 'Enabling…';
  const { configured } = getConfig();
  if (!configured) {
    result.className = 'test-result fail';
    result.textContent = 'Set the backend URL first.';
    return;
  }
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    result.className = 'test-result fail';
    result.textContent =
      "Push isn't supported here. On iPhone, add Alonarg to your Home Screen and open it from there (iOS 16.4+).";
    return;
  }
  try {
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      result.className = 'test-result fail';
      result.textContent = 'Notification permission was denied.';
      return;
    }
    const keyResp = await apiFetch('/api/push/key');
    if (!keyResp.ok) throw new Error('key ' + keyResp.status);
    const { key } = await keyResp.json();
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
    const subResp = await apiFetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub),
    });
    if (!subResp.ok) throw new Error('subscribe ' + subResp.status);
    result.className = 'test-result ok';
    result.textContent = 'Nudges enabled on this device.';
  } catch (err) {
    result.className = 'test-result fail';
    result.textContent = 'Could not enable nudges: ' + (err && err.message ? err.message : err);
  }
}

async function testNudge() {
  const result = $('#nudge-result');
  result.hidden = false;
  result.className = 'test-result';
  result.textContent = 'Sending…';
  try {
    const resp = await apiFetch('/api/push/test', { method: 'POST' });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.sent > 0) {
      result.className = 'test-result ok';
      result.textContent = 'Test nudge sent — check your notifications.';
    } else if (resp.ok && !data.total) {
      result.className = 'test-result fail';
      result.textContent = 'No subscribed devices yet. Tap "Enable on this device" first.';
    } else if (resp.ok) {
      result.className = 'test-result fail';
      result.textContent = 'Subscribed, but the push failed: ' + ((data.errors && data.errors[0]) || 'unknown error');
    } else {
      throw new Error('status ' + resp.status);
    }
  } catch (err) {
    result.className = 'test-result fail';
    result.textContent = 'Could not send a test nudge.';
  }
}

/* =====================================================================
 * Service worker
 * ===================================================================== */
function registerSW() {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js').catch((err) => {
        console.warn('Service worker registration failed:', err);
      });
    });
  }
}

/* =====================================================================
 * Wiring
 * ===================================================================== */
function init() {
  // Record button
  $('#rec-btn').addEventListener('click', () => {
    if (recState.recording) stopRecording();
    else startRecording();
  });

  // Tabs
  document.querySelectorAll('.tab').forEach((t) => {
    t.addEventListener('click', () => switchTab(t.dataset.tab));
  });

  // Sync now
  $('#sync-btn').addEventListener('click', () => trySync(true));

  // Settings
  $('#settings-form').addEventListener('submit', onSaveSettings);
  $('#test-btn').addEventListener('click', onTestConnection);

  // Record on laptop + meeting nudges
  $('#laptop-rec-btn').addEventListener('click', toggleLaptopRecording);
  $('#nudge-enable-btn').addEventListener('click', enableNudges);
  $('#nudge-test-btn').addEventListener('click', testNudge);

  // Calendar
  $('#cal-refresh').addEventListener('click', loadCalendar);
  $('#cal-list').addEventListener('change', (e) => {
    const cb = e.target.closest('input[type="checkbox"][data-key]');
    if (cb) onAutoRecordToggle(cb);
  });

  // Ask
  $('#ask-btn').addEventListener('click', doAsk);
  $('#ask-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); doAsk(); }
  });

  // Connection pill -> jump to settings
  $('#conn-pill').addEventListener('click', () => switchTab('settings'));

  // Detail back
  $('#detail-back').addEventListener('click', () => switchTab('recordings'));

  // Card delegation (open detail / retry)
  $('#cards').addEventListener('click', (e) => {
    const retry = e.target.closest('[data-retry]');
    if (retry) {
      e.stopPropagation();
      const id = retry.getAttribute('data-retry');
      dbGet(id).then((rec) => {
        if (rec) {
          rec.status = 'local';
          dbPut(rec).then(() => trySync(true));
        }
      });
      return;
    }
    const card = e.target.closest('[data-open]');
    if (card) {
      openDetail(card.getAttribute('data-open'));
    }
  });

  // React to connectivity changes
  window.addEventListener('online', () => {
    checkConnection().then((ok) => {
      if (ok) trySync();
    });
  });
  window.addEventListener('offline', () => checkConnection());

  // Re-check / refresh when app returns to foreground
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      checkConnection();
      refreshRecordings();
    }
  });

  registerSW();
  showView('record');

  // Initial load
  checkConnection().then((ok) => {
    refreshRecordings();
    pollLaptopStatus();
    if (ok) trySync();
  });

  // Periodic connection heartbeat
  _connTimer = setInterval(() => { checkConnection(); pollLaptopStatus(); }, 30000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
