"use strict";
// Alonarg dashboard client. Plain vanilla JS, no frameworks, no CDNs.

(function () {
  const STATUS_POLL_MS = 1500;
  const LIST_POLL_MS = 3000;
  // Statuses still being worked on -> keep polling the list until all are done.
  const ACTIVE = new Set(["recording", "transcribing", "summarizing"]);

  const recordBtn = document.getElementById("record-btn");
  const recordLabel = document.getElementById("record-label");
  const elapsedEl = document.getElementById("elapsed");

  let listPollTimer = null;

  // ---- helpers --------------------------------------------------------
  function fmtClock(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function fmtDuration(seconds) {
    seconds = Math.round(seconds || 0);
    if (seconds < 60) return seconds + "s";
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return s ? m + "m " + s + "s" : m + "m";
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  }

  // Shared secret injected by the server (empty/undefined when auth is off).
  const TOKEN = window.ALONARG_TOKEN || "";

  async function jsonFetch(url, opts) {
    opts = opts || {};
    if (TOKEN) {
      const headers = new Headers(opts.headers || {});
      headers.set("Authorization", "Bearer " + TOKEN);
      opts = Object.assign({}, opts, { headers: headers });
    }
    const res = await fetch(url, opts);
    let body = null;
    try { body = await res.json(); } catch (e) { /* ignore */ }
    return { ok: res.ok, status: res.status, body };
  }

  // <audio src="/audio/{id}"> can't send an Authorization header, so when a
  // token is set we authenticate via the ?token= query param instead.
  function authenticateAudio(root) {
    if (!TOKEN) return;
    (root || document).querySelectorAll("audio[data-audio-id]").forEach((el) => {
      const id = el.getAttribute("data-audio-id");
      if (!id) return;
      el.src = "/audio/" + id + "?token=" + encodeURIComponent(TOKEN);
    });
  }

  // Format any date / duration cells already on the page (server sends raw values).
  function formatStaticCells(root) {
    (root || document).querySelectorAll(".meta-date[data-created]").forEach((el) => {
      const formatted = fmtDate(el.getAttribute("data-created"));
      if (formatted) el.textContent = formatted;
    });
    (root || document).querySelectorAll(".meta-duration[data-duration]").forEach((el) => {
      el.textContent = fmtDuration(parseFloat(el.getAttribute("data-duration")));
    });
  }

  // ---- record button / status polling ---------------------------------
  function applyStatus(state) {
    if (!recordBtn) return;
    if (state.recording) {
      recordBtn.dataset.state = "recording";
      if (recordLabel) recordLabel.textContent = "Stop recording";
      if (elapsedEl) {
        elapsedEl.hidden = false;
        elapsedEl.textContent = fmtClock(state.elapsed_s);
      }
    } else {
      recordBtn.dataset.state = "idle";
      if (recordLabel) recordLabel.textContent = "Start recording";
      if (elapsedEl) { elapsedEl.hidden = true; elapsedEl.textContent = "00:00"; }
    }
  }

  async function pollStatus() {
    try {
      const { ok, body } = await jsonFetch("/api/status");
      if (ok && body) applyStatus(body);
    } catch (e) { /* network blip, ignore */ }
  }

  async function onRecordClick() {
    if (!recordBtn) return;
    recordBtn.disabled = true;
    try {
      const recording = recordBtn.dataset.state === "recording";
      const url = recording ? "/api/record/stop" : "/api/record/start";
      const { ok } = await jsonFetch(url, { method: "POST" });
      if (ok) {
        await pollStatus();
        if (recording) {
          // Just stopped -> a new recording is being processed; reload soon so
          // the new card shows up, then poll its status badge.
          setTimeout(() => window.location.reload(), 600);
        }
      }
    } catch (e) { /* ignore */ }
    finally { recordBtn.disabled = false; }
  }

  // ---- meeting nudge banner -------------------------------------------
  let _nudgeDismissed = null;
  async function pollNudge() {
    const banner = document.getElementById("nudge-banner");
    if (!banner) return;
    try {
      const { ok, body } = await jsonFetch("/api/nudge/status");
      if (!ok || !body) return;
      const live = body.live_meeting;
      const key = live ? (live.subject || "") + "|" + (live.start || "") : null;
      if (body.nudgeable && key && key !== _nudgeDismissed) {
        const txt = document.getElementById("nudge-text");
        if (txt) txt.textContent = "📅 “" + (live.subject || "A meeting") + "” is happening now — record it?";
        banner.dataset.key = key;
        banner.hidden = false;
      } else {
        banner.hidden = true;
      }
    } catch (e) { /* ignore */ }
  }

  // ---- calendar (Microsoft Graph) connect -----------------------------
  let _calPollTimer = null;
  async function refreshCalStatus() {
    const statusEl = document.getElementById("cal-status");
    const connectBtn = document.getElementById("cal-connect");
    const disconnectBtn = document.getElementById("cal-disconnect");
    if (!statusEl || !connectBtn || !disconnectBtn) return;
    try {
      const { ok, body } = await jsonFetch("/api/graph/status");
      if (!ok || !body) return;
      if (body.signed_in) {
        statusEl.textContent = "Connected" + (body.account ? " — " + body.account : "");
        connectBtn.hidden = true;
        disconnectBtn.hidden = false;
        const login = document.getElementById("cal-login");
        if (login) login.hidden = true;
      } else {
        statusEl.textContent = body.pending
          ? "Waiting for sign-in…"
          : "Not connected — needed for meeting sync & nudges.";
        connectBtn.hidden = false;
        disconnectBtn.hidden = true;
      }
    } catch (e) { /* ignore */ }
  }
  async function connectCalendar() {
    const btn = document.getElementById("cal-connect");
    const login = document.getElementById("cal-login");
    btn.disabled = true;
    try {
      const { ok, body } = await jsonFetch("/api/graph/login", { method: "POST" });
      if (!ok || !body || !body.user_code) {
        login.hidden = false;
        login.textContent = (body && body.detail) || "Could not start Microsoft sign-in.";
        btn.disabled = false;
        return;
      }
      const uri = body.verification_uri || "https://microsoft.com/devicelogin";
      login.hidden = false;
      login.innerHTML =
        'Go to <a href="' + uri + '" target="_blank" rel="noopener">' + uri +
        '</a> and enter code <strong>' + esc(body.user_code) + "</strong>, then sign in. (Waiting…)";
      try { window.open(uri, "_blank"); } catch (e) { /* popup blocked */ }
      if (_calPollTimer) clearInterval(_calPollTimer);
      _calPollTimer = setInterval(async () => {
        const r = await jsonFetch("/api/graph/status");
        if (r.ok && r.body && r.body.signed_in) {
          clearInterval(_calPollTimer); _calPollTimer = null;
          await refreshCalStatus();
          toast("Calendar connected");
        }
      }, 3000);
    } catch (e) {
      login.hidden = false;
      login.textContent = "Could not start Microsoft sign-in.";
    }
    btn.disabled = false;
  }
  async function disconnectCalendar() {
    await jsonFetch("/api/graph/logout", { method: "POST" });
    if (_calPollTimer) { clearInterval(_calPollTimer); _calPollTimer = null; }
    await refreshCalStatus();
    toast("Calendar disconnected");
  }

  // ---- left-nav view switching (recordings / calendar / settings) ------
  const VIEW_TITLES = { recordings: "Recordings", calendar: "Calendar", tasks: "Tasks", settings: "Settings" };
  function currentView() {
    const h = (location.hash || "").replace("#", "");
    return VIEW_TITLES[h] ? h : "recordings";
  }
  function showView(name) {
    document.querySelectorAll(".view[data-view]").forEach((s) => { s.hidden = s.dataset.view !== name; });
    document.querySelectorAll(".nav-link[data-view]").forEach((a) => { a.classList.toggle("active", a.dataset.view === name); });
    const t = document.getElementById("view-title");
    if (t) t.textContent = VIEW_TITLES[name] || "Recordings";
    // The calendar wants the full width; other views read better narrow.
    const main = document.querySelector(".content-main");
    if (main) main.classList.toggle("cal-wide", name === "calendar");
    if (name === "calendar") loadCalendar();
    if (name === "tasks") loadTasks();
    if (name === "settings") { refreshCalStatus(); loadSystemInfo(); }
  }

  // ---- tasks hub (all action items across meetings) -------------------
  async function loadTasks() {
    const body = document.getElementById("tasks-body");
    if (!body) return;
    const openOnly = document.getElementById("tasks-open-only");
    body.innerHTML = '<p class="muted">Loading…</p>';
    const q = openOnly && openOnly.checked ? "?open_only=true" : "";
    const { ok, body: data } = await jsonFetch("/api/action-items" + q);
    if (!ok || !Array.isArray(data)) { body.innerHTML = '<p class="ask-answer error">Could not load tasks.</p>'; return; }
    renderTasks(data);
  }
  function renderTasks(items) {
    const body = document.getElementById("tasks-body");
    const openOnly = document.getElementById("tasks-open-only");
    if (!items.length) {
      body.innerHTML = '<div class="empty-state"><p>No action items' + (openOnly && openOnly.checked ? " still open" : " yet") + ".</p></div>";
      return;
    }
    const order = [];
    const groups = {};
    items.forEach((it) => {
      if (!groups[it.recording_id]) { groups[it.recording_id] = { title: it.title, created: it.created_at, items: [] }; order.push(it.recording_id); }
      groups[it.recording_id].items.push(it);
    });
    body.innerHTML = "";
    order.forEach((rid) => {
      const g = groups[rid];
      const sec = document.createElement("section");
      sec.className = "panel task-group";
      const head = document.createElement("div");
      head.className = "task-group-head";
      head.innerHTML =
        '<a class="task-group-title" href="/recording/' + rid + '">' + esc(g.title) + "</a>" +
        '<span class="meta-date" data-created="' + esc(g.created) + '">' + esc(g.created) + "</span>";
      sec.appendChild(head);
      g.items.forEach((it) => {
        const row = document.createElement("label");
        row.className = "task-row" + (it.done ? " done" : "");
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.checked = it.done;
        const span = document.createElement("span");
        span.className = "task-text"; span.textContent = it.item;
        cb.addEventListener("change", () => toggleTask(it, cb, row));
        row.append(cb, span);
        sec.appendChild(row);
      });
      body.appendChild(sec);
    });
    formatStaticCells(body);
  }
  async function toggleTask(it, cb, row) {
    const done = cb.checked;
    cb.disabled = true;
    const { ok } = await jsonFetch("/api/action-items/toggle", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recording_id: it.recording_id, item: it.item, done: done }),
    });
    cb.disabled = false;
    if (ok) { it.done = done; row.classList.toggle("done", done); }
    else { cb.checked = !done; toast("Could not update"); }
  }

  // ---- calendar week view + auto-record toggles -----------------------
  let _calEvents = {};
  let _calLastEvents = null; // cached for re-render on resize
  // Minimum pixels per hour: keeps a 30-min slot tall enough to read its title.
  const MIN_HOUR_PX = 72;
  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }
  function elc(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
  function fmtHour(h) {
    const ampm = h < 12 ? "AM" : "PM";
    const hr = h % 12 === 0 ? 12 : h % 12;
    return hr + " " + ampm;
  }
  function fmtTimeRange(ev) {
    const s = new Date(ev.start), e = new Date(ev.end);
    const opt = { hour: "2-digit", minute: "2-digit" };
    const ss = isNaN(s.getTime()) ? "" : s.toLocaleTimeString(undefined, opt);
    const ee = isNaN(e.getTime()) ? "" : e.toLocaleTimeString(undefined, opt);
    return ss + (ee ? " – " + ee : "");
  }
  async function loadCalendar() {
    const body = document.getElementById("calendar-body");
    if (!body) return;
    body.innerHTML = '<p class="muted">Loading your calendar…</p>';
    const { ok, body: data } = await jsonFetch("/api/calendar/events?days=7");
    if (!ok || !data) {
      body.innerHTML = '<p class="ask-answer error">Could not load the calendar. Make sure the engine is running.</p>';
      return;
    }
    if (!data.connected) {
      body.innerHTML =
        '<div class="empty-state"><p>Calendar not connected.</p>' +
        '<p class="muted">Connect it in <a href="/#settings">Settings</a> to see your meetings here and have Alonarg auto-record them.</p></div>';
      return;
    }
    renderCalendar(data.events || []);
  }
  // Assign overlapping events to side-by-side columns (Outlook-style).
  // Each event gets .lane (0-based) and .cols (columns in its overlap cluster).
  function packDay(items) {
    items.sort((a, b) => a.sMin - b.sMin || a.eMin - b.eMin);
    let group = [], groupEnd = -Infinity;
    const flush = () => {
      const laneEnds = [];
      group.forEach((it) => {
        let i = 0;
        for (; i < laneEnds.length; i++) { if (it.sMin >= laneEnds[i]) break; }
        laneEnds[i] = it.eMin; it.lane = i;
      });
      group.forEach((it) => { it.cols = laneEnds.length; });
      group = [];
    };
    items.forEach((it) => {
      if (group.length && it.sMin >= groupEnd) { flush(); groupEnd = -Infinity; }
      group.push(it); groupEnd = Math.max(groupEnd, it.eMin);
    });
    flush();
  }

  function renderCalendar(events) {
    const body = document.getElementById("calendar-body");
    _calEvents = {};
    _calLastEvents = events;
    closePopover();
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const days = [];
    for (let i = 0; i < 7; i++) { const d = new Date(today); d.setDate(today.getDate() + i); days.push(d); }

    // Parse events into day buckets with minute offsets.
    const byDay = Array.from({ length: 7 }, () => []);
    let minHour = 8, maxHour = 18;
    events.forEach((ev) => {
      _calEvents[ev.key] = ev;
      const s = new Date(ev.start);
      if (isNaN(s.getTime())) return;
      let e = new Date(ev.end);
      if (isNaN(e.getTime()) || e <= s) e = new Date(s.getTime() + 30 * 60000);
      const di = days.findIndex((d) => sameDay(d, s));
      if (di < 0) return;
      let sMin = s.getHours() * 60 + s.getMinutes();
      let eMin = e.getHours() * 60 + e.getMinutes();
      if (!sameDay(s, e) || eMin <= sMin) eMin = 24 * 60; // clamp multi-day to end of day
      byDay[di].push({ ev: ev, sMin: sMin, eMin: eMin });
      minHour = Math.min(minHour, Math.floor(sMin / 60));
      maxHour = Math.max(maxHour, Math.ceil(eMin / 60));
    });
    const startHour = Math.max(0, minHour);
    const endHour = Math.min(24, Math.max(maxHour, startHour + 1));
    const totalHours = endHour - startHour;
    // Stretch the grid to fill the viewport height; never go below MIN_HOUR_PX
    // (so a 30-min meeting still shows its title). Scrolls if the day is long.
    const avail = Math.max(360, Math.floor(window.innerHeight - body.getBoundingClientRect().top - 24));
    const hourPx = Math.max(MIN_HOUR_PX, avail / totalHours);
    const totalPx = hourPx * totalHours;

    const grid = elc("div", "cal-grid");
    grid.appendChild(elc("div", "cal-corner"));
    days.forEach((d, idx) => {
      const h = elc("div", "cal-colhead" + (idx === 0 ? " today" : ""));
      h.innerHTML =
        '<span class="cal-dow">' + d.toLocaleDateString(undefined, { weekday: "short" }).toUpperCase() + "</span>" +
        '<span class="cal-dnum">' + (idx === 0 ? "Today" : d.toLocaleDateString(undefined, { day: "numeric", month: "short" })) + "</span>";
      grid.appendChild(h);
    });

    const gutter = elc("div", "cal-gutter"); gutter.style.height = totalPx + "px";
    for (let hr = startHour; hr < endHour; hr++) {
      const lbl = elc("div", "cal-hourlabel");
      lbl.style.top = (hr - startHour) * hourPx + "px";
      lbl.textContent = fmtHour(hr);
      gutter.appendChild(lbl);
    }
    grid.appendChild(gutter);

    days.forEach((d, idx) => {
      const col = elc("div", "cal-daycol" + (idx === 0 ? " today" : ""));
      col.style.height = totalPx + "px";
      col.style.backgroundSize = "100% " + hourPx + "px";
      packDay(byDay[idx]);
      byDay[idx].forEach((it) => col.appendChild(eventBlock(it, startHour, hourPx)));
      if (idx === 0) addNowLine(col, startHour, endHour, hourPx);
      grid.appendChild(col);
    });

    const week = elc("div", "cal-week");
    week.style.height = Math.min(avail, totalPx) + "px";
    week.appendChild(grid);
    body.innerHTML = "";
    body.appendChild(week);
  }

  function eventBlock(it, startHour, hourPx) {
    const ev = it.ev;
    const cols = it.cols || 1, lane = it.lane || 0;
    const top = (it.sMin - startHour * 60) / 60 * hourPx;
    const height = Math.max(20, (it.eMin - it.sMin) / 60 * hourPx);
    const el = elc("div", "cal-ev" + (ev.auto_record ? " armed" : ""));
    el.dataset.key = ev.key;
    el.style.top = top + "px";
    el.style.height = (height - 2) + "px";
    el.style.left = "calc(" + (lane / cols * 100) + "% + 2px)";
    el.style.width = "calc(" + (100 / cols) + "% - 4px)";
    el.title = ev.subject || "";
    el.innerHTML =
      '<span class="cal-ev-time">' + esc(fmtTimeRange(ev)) + "</span>" +
      '<span class="cal-ev-title">' + esc(ev.subject || "(no title)") + "</span>";
    el.addEventListener("click", (e) => { e.stopPropagation(); openEventPopover(ev, el); });
    return el;
  }

  function addNowLine(col, startHour, endHour, hourPx) {
    const now = new Date();
    const mins = now.getHours() * 60 + now.getMinutes();
    if (mins < startHour * 60 || mins > endHour * 60) return;
    const line = elc("div", "cal-now");
    line.style.top = (mins - startHour * 60) / 60 * hourPx + "px";
    col.appendChild(line);
  }

  // ---- event popover (peek + auto-record toggle) ----------------------
  let _popEl = null;
  function closePopover() {
    if (_popEl) { _popEl.remove(); _popEl = null; document.removeEventListener("click", _popOutside); }
  }
  function _popOutside(e) { if (_popEl && !_popEl.contains(e.target)) closePopover(); }
  function openEventPopover(ev, anchor) {
    closePopover();
    const pop = elc("div", "cal-pop");
    const n = (ev.attendees || []).length;
    pop.innerHTML =
      '<div class="cal-pop-title">' + esc(ev.subject || "(no title)") + "</div>" +
      '<div class="cal-pop-time muted">' + esc(fmtTimeRange(ev)) + "</div>" +
      (n ? '<div class="cal-pop-att muted">' + n + " attendee" + (n !== 1 ? "s" : "") + "</div>" : "") +
      '<label class="cal-pop-arm"><span class="switch"><input type="checkbox"' + (ev.auto_record ? " checked" : "") +
      '><span class="slider"></span></span><span>Auto-record this meeting</span></label>';
    document.body.appendChild(pop);
    const r = anchor.getBoundingClientRect();
    const W = 256;
    let left = window.scrollX + r.right + 8;
    if (left + W > window.scrollX + document.documentElement.clientWidth - 8) {
      left = window.scrollX + r.left - W - 8;
    }
    if (left < window.scrollX + 8) left = window.scrollX + 8;
    pop.style.left = left + "px";
    pop.style.top = (window.scrollY + Math.max(8, r.top)) + "px";
    const cb = pop.querySelector("input");
    cb.addEventListener("change", () => toggleAutoRecord(ev, cb, anchor));
    setTimeout(() => document.addEventListener("click", _popOutside), 0);
    _popEl = pop;
  }

  async function toggleAutoRecord(ev, cb, blockEl) {
    const enabled = cb.checked;
    cb.disabled = true;
    const { ok, body } = await jsonFetch("/api/calendar/autorecord", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event: ev, enabled: enabled }),
    });
    cb.disabled = false;
    if (ok && body) {
      ev.auto_record = enabled;
      if (blockEl) blockEl.classList.toggle("armed", enabled);
      toast(enabled ? "Will auto-record this meeting" : "Auto-record turned off");
    } else {
      cb.checked = !enabled;
      toast("Could not update auto-record");
    }
  }

  // ---- settings: engine/storage info + push test ----------------------
  async function loadSystemInfo() {
    const el = document.getElementById("sys-info");
    if (!el) return;
    const { ok, body } = await jsonFetch("/api/system/info");
    if (!ok || !body) { el.innerHTML = '<p class="muted">Could not load engine info.</p>'; return; }
    const cal = body.calendar_connected
      ? (body.calendar_source === "graph" ? "Microsoft Graph" : "Outlook desktop")
      : "Not connected";
    const rows = [
      ["Recordings", body.recordings_count],
      ["Summary model", body.model],
      ["Transcription", "Whisper " + body.whisper_model],
      ["Calendar", cal],
      ["Auto-record meetings", body.auto_record_count],
      ["Data folder", body.data_dir],
    ];
    el.innerHTML = rows.map((r) => "<dt>" + esc(r[0]) + "</dt><dd>" + esc(String(r[1])) + "</dd>").join("");
  }

  // ---- recordings list polling (status badges in place) ---------------
  function anyActive() {
    return Array.from(document.querySelectorAll(".card [data-status]"))
      .some((el) => ACTIVE.has(el.textContent.trim()));
  }

  function updateCard(rec) {
    const card = document.querySelector('.card[data-id="' + rec.id + '"]');
    if (!card) return;
    const badge = card.querySelector("[data-status]");
    if (badge && badge.textContent.trim() !== rec.status) {
      badge.textContent = rec.status;
      badge.className = "status-badge status-" + rec.status + "";
      badge.setAttribute("data-status", "");
    }
    const titleEl = card.querySelector(".card-title");
    if (titleEl && rec.title && titleEl.textContent !== rec.title) {
      titleEl.textContent = rec.title;
    }
  }

  async function pollList() {
    try {
      const { ok, body } = await jsonFetch("/api/recordings");
      if (ok && Array.isArray(body)) {
        body.forEach(updateCard);
      }
    } catch (e) { /* ignore */ }
    if (!anyActive() && listPollTimer) {
      clearInterval(listPollTimer);
      listPollTimer = null;
    }
  }

  function startListPolling() {
    if (listPollTimer) return;
    listPollTimer = setInterval(pollList, LIST_POLL_MS);
  }

  // ---- delete ---------------------------------------------------------
  async function onDeleteClick(btn) {
    const id = btn.getAttribute("data-id");
    if (!id) return;
    if (!window.confirm("Delete this recording?")) return;
    btn.disabled = true;
    const { ok, status } = await jsonFetch("/api/recordings/" + id, { method: "DELETE" });
    if (ok || status === 404) {
      const card = document.querySelector('.card[data-id="' + id + '"]');
      if (card) card.remove();
      const empty = document.getElementById("empty-state");
      if (empty && !document.querySelector(".card")) empty.hidden = false;
    } else {
      btn.disabled = false;
    }
  }

  // ---- small helpers (search / ask / export) --------------------------
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function debounce(fn, ms) {
    let t;
    return function () { clearTimeout(t); const a = arguments; t = setTimeout(() => fn.apply(null, a), ms); };
  }
  function toast(msg) {
    let el = document.getElementById("toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; document.body.appendChild(el); }
    el.textContent = msg; el.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove("show"), 1800);
  }

  // ---- search (dashboard) ---------------------------------------------
  function renderCard(rec) {
    const summary = rec.summary || {};
    const count = (summary.action_items && summary.action_items.length) || 0;
    const tags = (rec.state && rec.state.tags) || [];
    const pinned = !!(rec.state && rec.state.pinned);
    const li = document.createElement("li");
    li.className = "card";
    li.dataset.id = rec.id;
    li.dataset.href = "/recording/" + rec.id;
    li.dataset.tags = tags.join(",");
    li.innerHTML =
      '<div class="card-main">' +
        '<a class="card-title" href="/recording/' + rec.id + '">' + esc(rec.title || "Untitled recording") + "</a>" +
        '<div class="card-meta">' +
          '<span class="meta-date" data-created="' + esc(rec.created_at || "") + '">' + esc(rec.created_at || "") + "</span>" +
          '<span class="dot-sep">&middot;</span>' +
          '<span class="meta-duration" data-duration="' + (rec.duration_s || 0) + '">' + (rec.duration_s || 0) + "s</span>" +
        "</div>" +
        (summary.summary ? '<p class="card-summary">' + esc(summary.summary) + "</p>" : "") +
        '<div class="card-tags">' +
          '<span class="status-badge status-' + esc(rec.status) + '" data-status>' + esc(rec.status) + "</span>" +
          (count ? '<span class="action-count">' + count + " action item" + (count !== 1 ? "s" : "") + "</span>" : "") +
          tags.map((t) => '<span class="tag-chip sm">' + esc(t) + "</span>").join("") +
        "</div>" +
      "</div>" +
      '<div class="card-actions">' +
        '<button class="pin-btn card-pin" type="button" data-id="' + rec.id + '" data-pinned="' + (pinned ? "1" : "") + '" title="Pin meeting">★</button>' +
        '<button class="btn btn-danger delete-btn" type="button" data-id="' + rec.id + '">Delete</button>' +
      "</div>";
    return li;
  }
  function renderList(recs) {
    const ul = document.getElementById("recordings");
    if (!ul) return;
    ul.innerHTML = "";
    recs.forEach((r) => ul.appendChild(renderCard(r)));
    formatStaticCells(ul);
    refreshTagFilter();
    const empty = document.getElementById("empty-state");
    if (empty) {
      empty.hidden = recs.length > 0;
      if (recs.length === 0) {
        const input = document.getElementById("search-input");
        const q = input ? input.value.trim() : "";
        const p = empty.querySelector("p");
        if (p) p.textContent = q ? "No meetings match your search." : "No recordings yet.";
      }
    }
  }
  async function onSearch() {
    const input = document.getElementById("search-input");
    if (!input) return;
    const { ok, body } = await jsonFetch("/api/search?q=" + encodeURIComponent(input.value.trim()));
    if (ok && Array.isArray(body)) renderList(body);
  }

  // ---- filter recordings by tag --------------------------------------
  function refreshTagFilter() {
    const sel = document.getElementById("tag-filter");
    if (!sel) return;
    const tags = new Set();
    document.querySelectorAll("#recordings .card[data-tags]").forEach((c) => {
      (c.getAttribute("data-tags") || "").split(",").forEach((t) => { t = t.trim(); if (t) tags.add(t); });
    });
    const cur = sel.value;
    const sorted = Array.from(tags).sort((a, b) => a.localeCompare(b));
    sel.innerHTML = '<option value="">All tags</option>' +
      sorted.map((t) => '<option value="' + esc(t) + '">' + esc(t) + "</option>").join("");
    sel.value = sorted.includes(cur) ? cur : "";
    sel.hidden = sorted.length === 0;
    applyTagFilter();
  }
  function applyTagFilter() {
    const sel = document.getElementById("tag-filter");
    if (!sel) return;
    const want = (sel.value || "").toLowerCase();
    document.querySelectorAll("#recordings .card").forEach((c) => {
      const tags = (c.getAttribute("data-tags") || "").toLowerCase().split(",").map((s) => s.trim());
      c.style.display = !want || tags.includes(want) ? "" : "none";
    });
  }

  // ---- ask the local AI ------------------------------------------------
  async function doAsk(question, recId, answerEl, btn) {
    question = (question || "").trim();
    if (!question || !answerEl) return;
    if (btn) btn.disabled = true;
    answerEl.hidden = false;
    answerEl.className = "ask-answer";
    answerEl.textContent = "Thinking…";
    const payload = recId != null ? { question: question, recording_id: Number(recId) } : { question: question };
    const { ok, body } = await jsonFetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (ok && body) {
      answerEl.textContent = body.answer || "(no answer)";
    } else {
      answerEl.className = "ask-answer error";
      answerEl.textContent = (body && body.detail) || "Could not reach the local AI. Make sure the engine and Ollama are running.";
    }
    if (btn) btn.disabled = false;
  }

  // ---- copy / export notes (detail) -----------------------------------
  function collectList(id) {
    return Array.from(document.querySelectorAll("#" + id + " li"))
      .map((li) => (li.querySelector(".ai-text") || li).textContent.trim())
      .filter(Boolean);
  }
  function buildNotes() {
    const titleEl = document.getElementById("detail-title");
    const summaryEl = document.querySelector(".summary-text");
    const transcriptEl = document.querySelector(".transcript-text");
    const title = (titleEl ? titleEl.textContent : "Meeting").trim();
    const summary = (summaryEl ? summaryEl.textContent : "").trim();
    const transcript = (transcriptEl ? transcriptEl.textContent : "").trim();
    const actions = collectList("action-items");
    const next = collectList("next-steps");
    let md = "# " + title + "\n\n";
    if (summary) md += "## Summary\n" + summary + "\n\n";
    if (actions.length) md += "## Action items\n" + actions.map((a) => "- " + a).join("\n") + "\n\n";
    if (next.length) md += "## Next steps\n" + next.map((a) => "- " + a).join("\n") + "\n\n";
    if (transcript) md += "## Transcript\n" + transcript + "\n";
    return md;
  }
  function downloadText(filename, text) {
    const blob = new Blob([text], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ---- draft email for email-like action items (detail) ---------------
  const EMAIL_RE = /\b(e-?mail|send (a |an |the )?(note|message|mail|email|invite|quote|details?)|reply|respond|follow[- ]?up|write to|reach out|contact|get back to)\b/i;
  function setupEmailDrafts() {
    const main = document.querySelector(".detail");
    if (!main) return;
    const recId = main.getAttribute("data-rec-id");
    document.querySelectorAll("#action-items .action-item, #next-steps .ns-item").forEach((li) => {
      const span = li.querySelector(".ai-text");
      const text = span ? span.textContent.trim() : "";
      if (!text || !EMAIL_RE.test(text)) return;
      if (li.querySelector(".draft-email-btn")) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-ghost draft-email-btn";
      btn.textContent = "✉ Draft email";
      btn.addEventListener("click", () => draftEmail(li, text, recId, btn));
      li.appendChild(btn);
    });
  }
  async function draftEmail(li, item, recId, btn) {
    btn.disabled = true;
    let box = li.querySelector(".email-draft");
    if (!box) { box = document.createElement("div"); box.className = "email-draft"; li.appendChild(box); }
    box.innerHTML = '<p class="muted">Drafting…</p>';
    const payload = { item: item };
    if (recId != null) payload.recording_id = Number(recId);
    const { ok, body } = await jsonFetch("/api/draft-email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    btn.disabled = false;
    if (!ok || !body) {
      box.innerHTML = '<p class="ask-answer error">Could not draft the email. Make sure the engine and Ollama are running.</p>';
      return;
    }
    const subject = body.subject || "";
    const bodyText = body.body || "";
    box.innerHTML = "";
    const s = document.createElement("div");
    s.className = "email-subject";
    s.textContent = "Subject: " + subject;
    const pre = document.createElement("pre");
    pre.className = "email-body";
    pre.textContent = bodyText;
    const copy = document.createElement("button");
    copy.type = "button"; copy.className = "btn"; copy.textContent = "Copy";
    copy.addEventListener("click", () => { navigator.clipboard.writeText("Subject: " + subject + "\n\n" + bodyText); toast("Email copied"); });
    const open = document.createElement("a");
    open.className = "btn btn-accent"; open.textContent = "Open in email";
    open.href = "mailto:?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(bodyText);
    const actions = document.createElement("div");
    actions.className = "email-actions";
    actions.append(copy, open);
    box.append(s, pre, actions);
  }

  // ---- edit notes (summary / action items / next steps) ----------------
  let _notesOriginal = null;
  function startNotesEdit() {
    const region = document.getElementById("notes-region");
    if (!region || region.dataset.editing) return;
    region.dataset.editing = "1";
    _notesOriginal = region.innerHTML;
    const s = window.ALONARG_SUMMARY || {};
    region.innerHTML =
      '<section class="panel"><h3>Summary</h3><textarea id="edit-summary" class="edit-area" rows="4"></textarea></section>' +
      '<section class="panel"><h3>Action items <span class="muted">(one per line)</span></h3><textarea id="edit-actions" class="edit-area" rows="4"></textarea></section>' +
      '<section class="panel"><h3>Next steps <span class="muted">(one per line)</span></h3><textarea id="edit-next" class="edit-area" rows="3"></textarea></section>' +
      '<div class="notes-edit-actions"><button id="save-notes" class="btn btn-accent" type="button">Save</button><button id="cancel-notes" class="btn" type="button">Cancel</button></div>';
    document.getElementById("edit-summary").value = s.summary || "";
    document.getElementById("edit-actions").value = (s.action_items || []).join("\n");
    document.getElementById("edit-next").value = (s.next_steps || []).join("\n");
    document.getElementById("cancel-notes").addEventListener("click", cancelNotesEdit);
    document.getElementById("save-notes").addEventListener("click", saveNotesEdit);
  }
  function cancelNotesEdit() {
    const region = document.getElementById("notes-region");
    if (region && _notesOriginal != null) { region.innerHTML = _notesOriginal; delete region.dataset.editing; }
    setupEmailDrafts();
  }
  function linesOf(id) {
    return document.getElementById(id).value.split("\n").map((s) => s.trim()).filter(Boolean);
  }
  async function saveNotesEdit() {
    const saveBtn = document.getElementById("save-notes");
    saveBtn.disabled = true;
    const payload = {
      summary: document.getElementById("edit-summary").value,
      action_items: linesOf("edit-actions"),
      next_steps: linesOf("edit-next"),
    };
    const { ok } = await jsonFetch("/api/recordings/" + window.ALONARG_REC_ID + "/summary", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (ok) window.location.reload();
    else { saveBtn.disabled = false; toast("Could not save notes"); }
  }

  // ---- details sidebar (people / contacts) ----------------------------
  let _sideOriginal = null;
  function _personRow(name) {
    const row = document.createElement("div");
    row.className = "detail-row person-row";
    const input = document.createElement("input");
    input.type = "text"; input.className = "row-input person-name"; input.placeholder = "Name"; input.value = name || "";
    const rm = document.createElement("button");
    rm.type = "button"; rm.className = "row-remove"; rm.textContent = "×"; rm.title = "Remove";
    rm.addEventListener("click", () => row.remove());
    row.append(input, rm);
    return row;
  }
  function _contactRow(c) {
    c = c || {};
    const row = document.createElement("div");
    row.className = "detail-row contact-row";
    const name = document.createElement("input");
    name.type = "text"; name.className = "row-input c-name-in"; name.placeholder = "Name"; name.value = c.name || "";
    const email = document.createElement("input");
    email.type = "email"; email.className = "row-input c-email-in"; email.placeholder = "email@example.com"; email.value = c.email || "";
    const phone = document.createElement("input");
    phone.type = "text"; phone.className = "row-input c-phone-in"; phone.placeholder = "Phone"; phone.value = c.phone || "";
    const rm = document.createElement("button");
    rm.type = "button"; rm.className = "row-remove"; rm.textContent = "× Remove"; rm.title = "Remove contact";
    rm.addEventListener("click", () => row.remove());
    row.append(name, email, phone, rm);
    return row;
  }
  function startDetailsEdit() {
    const body = document.getElementById("side-body");
    if (!body || body.dataset.editing) return;
    body.dataset.editing = "1";
    _sideOriginal = body.innerHTML;
    const m = window.ALONARG_META || {};
    body.innerHTML =
      '<div class="side-block"><h4>People</h4><div id="people-rows"></div>' +
      '<button type="button" class="btn btn-ghost add-row" id="add-person">+ Add person</button></div>' +
      '<div class="side-block"><h4>Contacts</h4><div id="contacts-rows"></div>' +
      '<button type="button" class="btn btn-ghost add-row" id="add-contact">+ Add contact</button></div>' +
      '<div class="side-edit-actions"><button id="save-details" class="btn btn-accent" type="button">Save</button>' +
      '<button id="cancel-details" class="btn" type="button">Cancel</button></div>';
    const pr = document.getElementById("people-rows");
    const people = m.people || [];
    (people.length ? people : [""]).forEach((p) => pr.appendChild(_personRow(p)));
    const cr = document.getElementById("contacts-rows");
    const contacts = m.contacts || [];
    (contacts.length ? contacts : [{}]).forEach((c) => cr.appendChild(_contactRow(c)));
    document.getElementById("add-person").addEventListener("click", () => pr.appendChild(_personRow("")));
    document.getElementById("add-contact").addEventListener("click", () => cr.appendChild(_contactRow({})));
    document.getElementById("cancel-details").addEventListener("click", cancelDetailsEdit);
    document.getElementById("save-details").addEventListener("click", saveDetailsEdit);
  }
  function cancelDetailsEdit() {
    const body = document.getElementById("side-body");
    if (body && _sideOriginal != null) { body.innerHTML = _sideOriginal; delete body.dataset.editing; }
  }
  async function saveDetailsEdit() {
    const btn = document.getElementById("save-details");
    btn.disabled = true;
    const people = Array.from(document.querySelectorAll("#people-rows .person-name"))
      .map((i) => i.value.trim()).filter(Boolean);
    const contacts = Array.from(document.querySelectorAll("#contacts-rows .contact-row")).map((row) => ({
      name: row.querySelector(".c-name-in").value.trim(),
      email: row.querySelector(".c-email-in").value.trim(),
      phone: row.querySelector(".c-phone-in").value.trim(),
    })).filter((c) => c.name || c.email || c.phone);
    const { ok } = await jsonFetch("/api/recordings/" + window.ALONARG_REC_ID + "/meta", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ people: people, contacts: contacts }),
    });
    if (ok) window.location.reload();
    else { btn.disabled = false; toast("Could not save details"); }
  }
  async function detectDetails(btn) {
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = "Detecting…";
    const { ok } = await jsonFetch("/api/recordings/" + window.ALONARG_REC_ID + "/detect", { method: "POST" });
    if (ok) window.location.reload();
    else { btn.disabled = false; btn.textContent = orig; toast("Could not reach the local AI"); }
  }
  async function syncCalendar(btn) {
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = "Syncing…";
    const { ok, body } = await jsonFetch("/api/recordings/" + window.ALONARG_REC_ID + "/sync-calendar", { method: "POST" });
    if (ok && body && body.matched) {
      window.location.reload();
    } else if (ok && body && !body.matched) {
      btn.disabled = false; btn.textContent = orig; toast("No Outlook event found for this time");
    } else {
      btn.disabled = false; btn.textContent = orig;
      toast((body && body.detail) ? "Outlook not available" : "Sync failed");
    }
  }

  // ---- edit title (detail page) ---------------------------------------
  async function startTitleEdit() {
    const h2 = document.getElementById("detail-title");
    const editBtn = document.querySelector(".edit-title-btn");
    if (!h2 || document.querySelector(".title-edit")) return;
    const id = h2.getAttribute("data-id");
    const current = h2.textContent.trim();

    const input = document.createElement("input");
    input.type = "text";
    input.className = "title-input";
    input.value = current;
    input.maxLength = 200;
    const save = document.createElement("button");
    save.type = "button"; save.className = "btn btn-accent"; save.textContent = "Save";
    const cancel = document.createElement("button");
    cancel.type = "button"; cancel.className = "btn btn-ghost"; cancel.textContent = "Cancel";
    const editor = document.createElement("div");
    editor.className = "title-edit";
    editor.append(input, save, cancel);

    h2.hidden = true;
    if (editBtn) editBtn.hidden = true;
    h2.parentNode.insertBefore(editor, h2.nextSibling);
    input.focus();
    input.select();

    function cleanup() {
      editor.remove();
      h2.hidden = false;
      if (editBtn) editBtn.hidden = false;
    }
    async function doSave() {
      const title = input.value.trim();
      if (!title) { input.focus(); return; }
      save.disabled = true; cancel.disabled = true;
      const { ok, body } = await jsonFetch("/api/recordings/" + id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title }),
      });
      if (ok) {
        const newTitle = (body && body.title) || title;
        h2.textContent = newTitle;
        document.title = newTitle + " · Alonarg";
        cleanup();
      } else {
        save.disabled = false; cancel.disabled = false;
      }
    }
    save.addEventListener("click", doSave);
    cancel.addEventListener("click", cleanup);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doSave(); }
      else if (e.key === "Escape") { e.preventDefault(); cleanup(); }
    });
  }

  // ---- transcript <-> audio sync (detail) -----------------------------
  function setupTranscriptSync() {
    const segs = document.getElementById("transcript-segments");
    if (!segs) return;
    const audio = document.querySelector("audio[data-audio-id]");
    segs.querySelectorAll(".tseg-ts[data-seek]").forEach((b) => {
      b.textContent = fmtClock(parseFloat(b.getAttribute("data-seek")) || 0);
    });
    segs.addEventListener("click", (e) => {
      const b = e.target.closest(".tseg-ts");
      if (!b || !audio) return;
      audio.currentTime = parseFloat(b.getAttribute("data-seek")) || 0;
      audio.play().catch(() => {});
    });
    if (audio) {
      const rows = Array.from(segs.querySelectorAll(".tseg"));
      audio.addEventListener("timeupdate", () => {
        const t = audio.currentTime;
        let active = null;
        for (const r of rows) {
          if (t >= (parseFloat(r.dataset.start) || 0) && t < (parseFloat(r.dataset.end) || 0)) { active = r; break; }
        }
        rows.forEach((r) => r.classList.toggle("active", r === active));
      });
    }
  }

  // ---- regenerate summary with a template (detail) --------------------
  async function regenerate(btn) {
    const sel = document.getElementById("tmpl-select");
    const tmpl = sel ? sel.value : "general";
    btn.disabled = true; const orig = btn.textContent; btn.textContent = "Regenerating…";
    const { ok, body } = await jsonFetch("/api/recordings/" + window.ALONARG_REC_ID + "/resummarize", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template: tmpl }),
    });
    if (ok) { window.location.reload(); }
    else { btn.disabled = false; btn.textContent = orig; toast((body && body.detail) || "Could not regenerate (is Ollama running?)"); }
  }

  // ---- pin toggle (detail) --------------------------------------------
  async function togglePin(btn) {
    const next = btn.getAttribute("data-pinned") !== "1";
    btn.disabled = true;
    const { ok } = await jsonFetch("/api/recordings/" + btn.getAttribute("data-id") + "/state", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pinned: next }),
    });
    btn.disabled = false;
    if (ok) { btn.setAttribute("data-pinned", next ? "1" : ""); toast(next ? "Pinned" : "Unpinned"); }
    else toast("Could not update");
  }

  // ---- tags (detail) --------------------------------------------------
  function renderTags() {
    const box = document.getElementById("tags-box");
    if (!box) return;
    const tags = (window.ALONARG_STATE || {}).tags || [];
    box.innerHTML = tags.length ? "" : '<span class="muted">No tags yet.</span>';
    tags.forEach((t) => {
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      chip.innerHTML = esc(t) + ' <button class="tag-x" type="button" title="Remove">×</button>';
      chip.querySelector(".tag-x").addEventListener("click", () => saveTags(tags.filter((x) => x !== t)));
      box.appendChild(chip);
    });
  }
  async function saveTags(tags) {
    const box = document.getElementById("tags-box");
    const { ok, body } = await jsonFetch("/api/recordings/" + box.getAttribute("data-id") + "/state", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tags: tags }),
    });
    if (ok) { window.ALONARG_STATE.tags = (body && body.tags) || tags; renderTags(); }
    else toast("Could not save tags");
  }
  function addTag(name) {
    name = (name || "").trim();
    if (!name) return;
    const tags = ((window.ALONARG_STATE || {}).tags || []).slice();
    if (tags.some((t) => t.toLowerCase() === name.toLowerCase())) return;
    tags.push(name);
    saveTags(tags);
  }

  // ---- wiring ---------------------------------------------------------
  document.addEventListener("click", (ev) => {
    const del = ev.target.closest(".delete-btn");
    if (del) { ev.preventDefault(); onDeleteClick(del); return; }
    const pin = ev.target.closest(".card-pin");
    if (pin) { ev.preventDefault(); ev.stopPropagation(); togglePin(pin); return; }
    // Clickable card: open the recording unless an interactive element was clicked.
    const card = ev.target.closest(".card[data-href]");
    if (card && !ev.target.closest("a, button, input, textarea")) {
      window.location.href = card.getAttribute("data-href");
    }
  });

  if (recordBtn) recordBtn.addEventListener("click", onRecordClick);

  const editTitleBtn = document.querySelector(".edit-title-btn");
  if (editTitleBtn) editTitleBtn.addEventListener("click", startTitleEdit);

  // search (dashboard)
  const searchInput = document.getElementById("search-input");
  if (searchInput) searchInput.addEventListener("input", debounce(onSearch, 250));
  const tagFilter = document.getElementById("tag-filter");
  if (tagFilter) tagFilter.addEventListener("change", applyTagFilter);

  // meeting nudge banner
  const nudgeRecord = document.getElementById("nudge-record");
  if (nudgeRecord) nudgeRecord.addEventListener("click", async () => {
    nudgeRecord.disabled = true;
    try { await jsonFetch("/api/record/start", { method: "POST" }); } catch (e) { /* ignore */ }
    const b = document.getElementById("nudge-banner"); if (b) b.hidden = true;
    await pollStatus();
    nudgeRecord.disabled = false;
  });
  const nudgeDismiss = document.getElementById("nudge-dismiss");
  if (nudgeDismiss) nudgeDismiss.addEventListener("click", () => {
    const b = document.getElementById("nudge-banner");
    if (b) { _nudgeDismissed = b.dataset.key || "x"; b.hidden = true; }
  });

  // calendar connect
  const calConnect = document.getElementById("cal-connect");
  if (calConnect) calConnect.addEventListener("click", connectCalendar);
  const calDisconnect = document.getElementById("cal-disconnect");
  if (calDisconnect) calDisconnect.addEventListener("click", disconnectCalendar);

  // tasks: open-only filter
  const tasksOpenOnly = document.getElementById("tasks-open-only");
  if (tasksOpenOnly) tasksOpenOnly.addEventListener("change", loadTasks);

  // settings: send test push
  const pushTestBtn = document.getElementById("push-test");
  if (pushTestBtn) pushTestBtn.addEventListener("click", async () => {
    pushTestBtn.disabled = true;
    const { ok, body } = await jsonFetch("/api/push/test", { method: "POST" });
    pushTestBtn.disabled = false;
    if (ok && body && body.sent > 0) toast("Test notification sent");
    else if (ok && body && !body.total) toast("No phone subscribed yet — enable nudges in the PWA");
    else toast("Could not send the test notification");
  });

  // ask across all meetings (dashboard)
  const gAskBtn = document.getElementById("global-ask-btn");
  const gAskInput = document.getElementById("global-ask-input");
  const gAskAnswer = document.getElementById("global-ask-answer");
  if (gAskBtn && gAskInput && gAskAnswer) {
    gAskBtn.addEventListener("click", () => doAsk(gAskInput.value, null, gAskAnswer, gAskBtn));
    gAskInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doAsk(gAskInput.value, null, gAskAnswer, gAskBtn); } });
  }

  // ask about this meeting (detail)
  const askBtn = document.getElementById("ask-btn");
  const askInput = document.getElementById("ask-input");
  const askAnswer = document.getElementById("ask-answer");
  const detailMain = document.querySelector(".detail");
  if (askBtn && askInput && askAnswer && detailMain) {
    const rid = detailMain.getAttribute("data-rec-id");
    askBtn.addEventListener("click", () => doAsk(askInput.value, rid, askAnswer, askBtn));
    askInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doAsk(askInput.value, rid, askAnswer, askBtn); } });
  }

  // copy / download notes (detail)
  const copyBtn = document.getElementById("copy-notes");
  if (copyBtn) copyBtn.addEventListener("click", () => { navigator.clipboard.writeText(buildNotes()).then(() => toast("Notes copied")); });
  const dlBtn = document.getElementById("download-notes");
  if (dlBtn) dlBtn.addEventListener("click", () => {
    const titleEl = document.getElementById("detail-title");
    const title = (titleEl ? titleEl.textContent : "meeting").trim();
    const safe = title.replace(/[^a-z0-9]+/gi, "-").replace(/^-+|-+$/g, "").toLowerCase() || "meeting";
    downloadText(safe + ".md", buildNotes());
  });

  // email drafts on email-like action items + next steps (detail)
  setupEmailDrafts();

  // transcript<->audio sync, regenerate, pin, tags (detail)
  setupTranscriptSync();
  const regenBtn = document.getElementById("regen-btn");
  if (regenBtn) regenBtn.addEventListener("click", () => regenerate(regenBtn));
  const pinBtn = document.getElementById("pin-btn");
  if (pinBtn) pinBtn.addEventListener("click", () => togglePin(pinBtn));
  const tagInput = document.getElementById("tag-input");
  if (tagInput) {
    renderTags();
    tagInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); addTag(tagInput.value); tagInput.value = ""; }
    });
  }

  // edit notes + details sidebar (detail)
  const editNotesBtn = document.getElementById("edit-notes");
  if (editNotesBtn) editNotesBtn.addEventListener("click", startNotesEdit);
  const editDetailsBtn = document.getElementById("edit-details-btn");
  if (editDetailsBtn) editDetailsBtn.addEventListener("click", startDetailsEdit);
  const detectBtn = document.getElementById("detect-btn");
  if (detectBtn) detectBtn.addEventListener("click", () => detectDetails(detectBtn));
  const syncBtn = document.getElementById("sync-btn");
  if (syncBtn) syncBtn.addEventListener("click", () => syncCalendar(syncBtn));

  formatStaticCells(document);
  authenticateAudio(document);

  // Only the dashboard has the record button / cards list / views.
  if (recordBtn || document.getElementById("recordings")) {
    pollStatus();
    setInterval(pollStatus, STATUS_POLL_MS);
    if (anyActive()) startListPolling();
    refreshTagFilter();
    pollNudge();
    setInterval(pollNudge, 20000);

    // left-nav views: switch on hash change, show the current one on load
    if (document.querySelector(".view[data-view]")) {
      window.addEventListener("hashchange", () => showView(currentView()));
      showView(currentView());
      // re-fit the calendar grid to the window when it resizes
      window.addEventListener("resize", debounce(() => {
        const cal = document.querySelector('.view[data-view="calendar"]');
        if (cal && !cal.hidden && _calLastEvents) renderCalendar(_calLastEvents);
      }, 200));
    }
  }
})();
