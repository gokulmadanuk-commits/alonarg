# Connecting the phone PWA to your PC

The phone app (**https://alonarg.vercel.app**) is just the front door. The actual
transcription (Whisper) and summarization (a small **local LLM** via Ollama) run on **your PC**,
where the models live. So the phone needs a way to reach your PC.

```
 📱 https://alonarg.vercel.app   ──HTTPS──►   🖥️ your PC : Alonarg engine (port 8765)
   record + queue offline                       transcribe + local-LLM summary + same dashboard
```

## 1. Install the app on your phone (do this now, works offline)
1. Open **https://alonarg.vercel.app** in your phone browser.
2. **Add to Home Screen** (iOS Safari: Share → Add to Home Screen; Android Chrome: ⋮ → Install app).
3. You can already record meetings — they're saved on the phone and will upload once the PC is reachable.

## 2. Recommended: Tailscale (stable, private — what this setup uses)

Tailscale is a free private network. Your laptop and phone join it, and the phone reaches the
engine over an HTTPS address that **never changes** and is visible only to your own devices.

**Laptop (one-time):**
1. `winget install Tailscale.Tailscale`, then sign in: `tailscale up` (opens a browser).
2. Enable HTTPS/Serve for your tailnet — click the link the CLI prints and press **Enable**.
3. Expose the engine: `tailscale serve --bg 8765` → gives `https://<machine>.<tailnet>.ts.net`.

**Phone (one-time):** install the Tailscale app and sign in with the **same account**.

**In the PWA → Settings:** set **Backend URL** to your `https://<machine>.<tailnet>.ts.net`
address (no token needed — Tailscale already limits access to your own devices), then
**Test connection** → **Save**.

Everything persists across reboots: the engine auto-starts (Startup folder), Tailscale
auto-starts, and the serve config is remembered.

## 3. Alternative: free Cloudflare tunnel (changes on restart)

Because the PWA is served over HTTPS, the phone can't talk to a plain `http://192.168.x.x` LAN
address (browsers block mixed content). The quick alternative is a free HTTPS tunnel. A helper does it all:

```powershell
.\connect.ps1
```

This will:
1. Generate (or reuse) a secret **token** so only you can reach your engine — and print it.
2. Start the Alonarg engine on `127.0.0.1:8765` if it isn't already running (with the token).
3. Install **cloudflared** via winget if needed.
4. Open a Cloudflare tunnel and print an `https://<random>.trycloudflare.com` URL.

Then on the phone, open the PWA → **Settings**:
- **Backend URL** = the `https://<random>.trycloudflare.com` URL
- **Token** = the token printed by the script
- Tap **Test connection** (should say connected), then **Save**.

Now go to **Recordings → Sync now**: your phone recordings upload, get transcribed +
summarized on the PC, and appear in the same dashboard as your desktop recordings.

## Notes & alternatives
- **Stable URL:** `trycloudflare` URLs change each run. For a permanent address, set up a
  named Cloudflare Tunnel (free, needs a domain) or use **ngrok** (`ngrok http 8765`) or
  **Tailscale** (private VPN; `tailscale serve https / http://localhost:8765`).
- **Security:** always keep a token set when exposing the engine. The token gates every
  `/api/*` and `/audio/*` request (`Authorization: Bearer <token>`, or `?token=` for audio).
  Set it permanently so the desktop app and the tunnel share it:
  `setx ALONARG_TOKEN "your-long-random-secret"` (reopen your terminal afterward).
- **Same-WiFi without a tunnel:** only works if you instead open the PC's *own* dashboard
  (`http://<PC-LAN-IP>:8765`, after setting `ALONARG_HOST=0.0.0.0`) directly in the phone
  browser — not through the Vercel PWA. The tunnel is the recommended path.
- **PC must be on** to transcribe/summarize. The phone happily queues recordings offline and
  syncs them whenever the engine is reachable, so you can record anywhere.
