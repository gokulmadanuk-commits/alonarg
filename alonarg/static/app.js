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
    const li = document.createElement("li");
    li.className = "card";
    li.dataset.id = rec.id;
    li.dataset.href = "/recording/" + rec.id;
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
        "</div>" +
      "</div>" +
      '<div class="card-actions">' +
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
    document.querySelectorAll("#action-items .action-item").forEach((li) => {
      const span = li.querySelector(".ai-text");
      const text = span ? span.textContent.trim() : "";
      if (!text || !EMAIL_RE.test(text)) return;
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

  // search (dashboard)
  const searchInput = document.getElementById("search-input");
  if (searchInput) searchInput.addEventListener("input", debounce(onSearch, 250));

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

  // email drafts on email-like action items (detail)
  setupEmailDrafts();

  formatStaticCells(document);
  authenticateAudio(document);

  // Only the dashboard has the record button / cards list.
  if (recordBtn || document.getElementById("recordings")) {
    pollStatus();
    setInterval(pollStatus, STATUS_POLL_MS);
    if (anyActive()) startListPolling();
  }
})();
