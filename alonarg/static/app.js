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

  // ---- wiring ---------------------------------------------------------
  document.addEventListener("click", (ev) => {
    const del = ev.target.closest(".delete-btn");
    if (del) { ev.preventDefault(); onDeleteClick(del); return; }
    // Clickable card: open the recording unless an interactive element was clicked.
    const card = ev.target.closest(".card[data-href]");
    if (card && !ev.target.closest("a, button, input, textarea")) {
      window.location.href = card.getAttribute("data-href");
    }
  });

  if (recordBtn) recordBtn.addEventListener("click", onRecordClick);

  const editTitleBtn = document.querySelector(".edit-title-btn");
  if (editTitleBtn) editTitleBtn.addEventListener("click", startTitleEdit);

  formatStaticCells(document);
  authenticateAudio(document);

  // Only the dashboard has the record button / cards list.
  if (recordBtn || document.getElementById("recordings")) {
    pollStatus();
    setInterval(pollStatus, STATUS_POLL_MS);
    if (anyActive()) startListPolling();
  }
})();
