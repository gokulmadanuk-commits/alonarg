# Connecting the phone PWA to your PC

The phone app (**https://alonarg.vercel.app**) is just the front door. The actual
transcription and the Claude-plan summarization run on **your PC** — that's the only way to
honor "use my plan, not the API" (your Claude subscription is tied to this machine and can't
run in the cloud). So the phone needs a way to reach your PC.

```
 📱 https://alonarg.vercel.app   ──HTTPS──►   🖥️ your PC : Alonarg engine (port 8765)
   record + queue offline                       transcribe + claude summary + same dashboard
```

## 1. Install the app on your phone (do this now, works offline)
1. Open **https://alonarg.vercel.app** in your phone browser.
2. **Add to Home Screen** (iOS Safari: Share → Add to Home Screen; Android Chrome: ⋮ → Install app).
3. You can already record meetings — they're saved on the phone and will upload once the PC is reachable.

## 2. Expose your PC to the phone (one command when you're back at the PC)

Because the PWA is served over HTTPS, the phone can't talk to a plain `http://192.168.x.x` LAN
address (browsers block mixed content). The clean fix is a free HTTPS tunnel. A helper does it all:

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
