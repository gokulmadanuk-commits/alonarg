# Alonarg — Ideas & Backlog

A running list of things to build. Local-only product, just for me — **not** for sale.
Everything here runs on my laptop with the local model (no API costs).

## ✅ Shipped
- Local recording (mic + system audio), local transcription (faster-whisper), local-LLM
  summaries/action items/next steps (Ollama `llama3.2:3b`).
- Light-mode dashboard + offline-first phone PWA (Vercel) connected over Tailscale.
- Auto-start at login; runs while screen locked.
- Clickable cards; edit meeting title.
- Search across titles/summaries/transcripts.
- Ask your meetings (per-meeting + across-all), incl. exact metadata counts ("how many
  have no action items").
- Copy notes / Download .md.
- Draft email for email-like action items **and** next steps (local-LLM → subject/body, copy / open in mail).
- Edit notes (summary / action items / next steps).
- Details sidebar: people + contacts (manual edit + local-LLM "Detect").

## 🔨 In progress
- **Outlook desktop calendar sync** — match a recording to its calendar event (local COM,
  no OAuth) → auto-title + real attendee names/emails into the Details pane.
- **Structured contact rows** in the Details editor (separate name/email/phone fields, add/remove)
  instead of one text box.

## ⏭️ Next up (agreed)
- **Global action-items hub** — one page with every open action item across meetings, with
  checkboxes to tick them off.
- **Tags / folders** — organize recordings; filter the dashboard by tag.
- **Mirror Search + Ask to the phone PWA** — same features on mobile; the phone calls the
  laptop, which answers with the local model.

## 💡 Later / someday
- Auto-run Detect + Calendar sync during processing (so new meetings arrive pre-enriched).
- Outlook **draft creation** (drop a ready draft into Outlook via COM) instead of just `mailto:`.
- Action items → Outlook Tasks / To-Do, or calendar follow-up holds.
- Speaker rename (replace "You"/"Others" with real names, e.g. from calendar attendees).
- Custom summary templates (1:1 / standup / sales-call).
- Pin / favorite meetings; archive + retention/storage management.
- Semantic search (local embeddings via Ollama) for fuzzy "find meetings about X".
- Gmail integration (personal email) — back burner; Outlook/work first.

## 🚫 Not planning (for now)
- Packaging/installer + selling as a product (revisit later if ever).
