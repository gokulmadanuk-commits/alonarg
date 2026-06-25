# Alonarg — Ideas & Backlog

A running list of things to build. Local-only product, just for me — **not** for sale.
Everything here runs on my laptop with the local model (no API costs).

## ✅ Shipped
- Local recording (mic + system audio), local transcription (faster-whisper), local-LLM
  summaries/action items/next steps (Ollama `llama3.2:3b`).
- Light-mode dashboard with a left nav (Recordings / Calendar / Tasks / Settings) +
  offline-first phone PWA (Vercel) connected over Tailscale.
- Auto-start at login; runs while screen locked.
- Clickable cards; edit meeting title.
- Search across titles/summaries/transcripts.
- Ask your meetings (per-meeting + across-all), incl. exact metadata counts.
- Copy notes / Download .md.
- Draft email for email-like action items **and** next steps (local-LLM → subject/body).
- Edit notes (summary / action items / next steps).
- Details sidebar: people + contacts (manual edit + local-LLM "Detect").
- Calendar via Microsoft Graph (new Outlook) with classic-Outlook COM fallback:
  event title + attendees into Details; **auto-record** pre-approved meetings; live
  meeting **nudges** (phone Web Push + dashboard banner).
- **Calendar week view** (Outlook-style time-grid) with per-meeting Auto-record toggles.
- **Note templates** — summarize as General / Standup / 1:1 / Sales / Interview, with a
  one-click **Regenerate**.
- **Transcript ↔ audio sync** — clickable, speaker-labeled transcript; click a timestamp
  to seek; the current line highlights during playback.
- **Talk-time** bars (You vs Others) on the meeting page.
- **Tasks hub** — every action item across all meetings, grouped by meeting, tick to
  complete, "open only" filter.
- **Tags + pin** — tag meetings, filter the dashboard by tag, pin favourites to the top.

## 🔨 In progress
- (nothing right now)

## ⏭️ Next up (agreed)
- **Mirror Search + Ask to the phone PWA** — same features on mobile; the phone calls the
  laptop, which answers with the local model. (Needs a PWA redeploy to Vercel.)

## 💡 Later / someday — with local feasibility (from competitor research, 2026)
Rated for building **fully local & free** on the current stack (faster-whisper + Ollama
`llama3.2:3b` + SQLite). Easy/Medium are the realistic wins.
- **Pre-meeting brief** (Easy) — before a meeting, assemble attendees + prior summaries for
  the same people/series and have the LLM write a one-paragraph "here's where you left off";
  push to the phone via the existing nudge channel. *(Granola/Fellow.)*
- **Auto-enrich on processing** (Easy) — run calendar-match (and optionally Detect) during
  the pipeline so new meetings arrive pre-titled with people/contacts.
- **Keyword / topic trackers across meetings** (Easy) — define tracked terms, count
  mentions across all meetings over the existing search index. *(Fireflies/Avoma.)*
- **Recurring-series grouping** (Easy) — group meetings by the Graph recurrence/series id
  (a "thread" per recurring meeting). *(Fellow/tl;dv.)*
- **Self-coaching metrics** (Easy) — questions asked, longest monologue, filler-word count
  from the transcript (per-speaker via the mic/system split). *(Avoma.)*
- **Topic / chapter segmentation + time-per-topic** (Medium) — LLM segments the transcript
  into topics with timestamps (map-reduce for long calls); clickable chapters reuse the
  transcript-sync UI. *(Avoma/tl;dv.)*
- **Semantic search + cited answers** (Medium) — local embeddings (`nomic-embed-text` via
  Ollama) in SQLite (`sqlite-vec`); fuzzy "find meetings about X" and Q&A answers that link
  back to the exact transcript moment. Adds one small model + dep. *(Otter/Granola.)*
- **Cross-meeting rollups** (Medium) — weekly "what happened" digest by map-reduce over
  per-meeting summaries. *(tl;dv multi-meeting intelligence.)*
- **Sentiment per chapter** (Medium) — coarse tone labels from the LLM. *(tl;dv/Fireflies.)*
- **Redaction / PII masking** (Medium) — regex for emails/phones (easy) + local NER for
  names (medium). Strong fit for a privacy-first local app.
- **Multi-language + translate-to-English** (Easy/Medium) — Whisper is already multilingual;
  expose language detect + `task=translate`. Bigger model improves non-English accuracy.
- **Outlook draft creation** (Medium) — drop a ready draft into Outlook via COM/Graph
  instead of `mailto:`.
- **Action items → To-Do / .ics holds** (Medium) — export tasks to Microsoft To-Do or as
  calendar follow-ups.
- **Speaker rename** (Medium) — replace "You"/"Others" with real names (e.g. from calendar
  attendees) across the transcript + talk-time.
- **Highlights / clips** (Medium) — mark moments (post-hoc easy; live "magic highlight" via
  the phone), optionally cut audio with ffmpeg. *(Fathom/Bluedot.)*
- **MCP server over the meeting store** (Medium) — let local AI tools query Alonarg's
  meetings. *(Otter.)*
- **Pin/archive + retention/storage management** (Easy) — archive old meetings, prune audio.

## 🧱 Hard (possible locally, but heavy — deferred)
- **Speaker diarization** ("who said what" beyond the mic/system split) — pyannote on CPU is
  a heavy, gated model download. The free win we already have: mic vs system channel split.
- **Recurring-voice / speaker memory** (name a voice once, recognised later) — needs speaker
  embeddings + a voiceprint DB; depends on diarization.
- **Real-time live transcription / live coaching** — faster-whisper *small* on CPU isn't
  comfortably real-time; we already do async live nudges.

## 🚫 Not planning / not feasible
- Packaging/installer + selling as a product.
- In-meeting **bots** that join Zoom/Teams/Meet (no bots — WASAPI system capture covers it).
- **AI agents pushing to CRM / Slack / external tools** (needs paid 3rd-party integrations).
  Local substitute: write a Markdown / `.ics` / to-do file.
- **Video/facial engagement scoring** (Read.ai) — audio-only by design.
- Capturing system / other-party audio on the **phone** (mobile browsers expose only the
  mic). Workaround: "Record on laptop".
