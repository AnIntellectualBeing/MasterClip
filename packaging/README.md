# Building Clip Studio's Setup.exe

```
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

Output: `dist\ClipStudioSetup-1.0.0.exe`. It's a per-user install, so no admin prompt. It goes to
`%LOCALAPPDATA%\Programs\Clip Studio` with a Start menu entry and an optional desktop icon.

## One-time setup on the build PC
- `.venv` with `requirements.txt` plus `pyinstaller` (`pip install pyinstaller`).
- Inno Setup 6: `winget install JRSoftware.InnoSetup --scope user`.
- `%USERPROFILE%\ClipStudioBuild\vendor\` must hold:
  - `bin\ffmpeg.exe`, `bin\ffprobe.exe`: a static FFmpeg build. The current one is gyan.dev essentials, which is GPL because it includes libx264.
  - `bin\deno.exe`: Deno v2.9.8 from github.com/denoland/deno/releases. yt-dlp needs it for YouTube.
  - `FFMPEG-LICENSE.txt`: the licence file that comes with the FFmpeg build.

Work files go to `%USERPROFILE%\ClipStudioBuild` because C: has room and Z: doesn't. The build refuses to
package `.env`, `*.key`, private-key `.pem` files, `posting.json` and `ai.json`.

## What's inside
- `ClipStudio.exe`: the window (pywebview / WebView2), with no console.
- `clipstudio-ytdlp.exe`: yt-dlp as a hidden console program, run with `--js-runtimes deno:<bundled deno>`.
- `_internal\`: Python, `static\`, `fonts\`, `models\`, `bin\` (ffmpeg, ffprobe, deno).
- The Whisper speech model downloads on first use (Hugging Face cache in the user profile).
- Buyer data: `%LOCALAPPDATA%\Clip Studio\` (projects, settings, log). Buyer keys (Zernio, Claude, licence)
  and the trial count are in Windows Credential Manager under "Clip Studio". An uninstall keeps both.

QA mode: `ClipStudio.exe --headless` starts only the engine. The URL is written to `%LOCALAPPDATA%\Clip Studio\app.log`.

## Licence keys
- The app holds only the **public** key (`clipstudio/licence.py PUBLIC_KEY`).
- The private key was made on 22 Nov 2026 at `C:\Users\E27\ClipStudio-licence\clipstudio-private.pem`.
  **Back it up somewhere safe.** If it's lost, no new keys can be issued for this public key. If it leaks, anyone can make keys.
- Issue a key by hand: `python tools/licence_admin.py issue --private <pem> --order <order id> --seats 2`.
- Key format: `CS1-…` (Ed25519-signed `{"id","s","t","v"}`). It works offline. A typo or another seller's key is refused.
- Free trial: 3 new videos without a key (`TRIAL_VIDEOS`). Editing, long videos and posting from an existing
  project stay free.
- Shop + 2-PC limit: `server/licence-worker` is a Cloudflare Worker that sells with Razorpay (the buy page, key on
  the page, a webhook backup, a lost-key page) and checks seats. It isn't deployed; the steps are in its README.
  Once it's deployed, set `LICENCE_API` in **`clipstudio/shop.py`** and rebuild. The Buy button, the
  "lost your key?" link and the 2-PC limit then appear. (Environment variables are read on the buyer's PC, not at
  build time, so they're for QA only.) To sell somewhere else instead, set `BUY_URL` in `shop.py`.

## Not done yet
- Code signing. Unsigned, Windows SmartScreen shows "Windows protected your PC → More info → Run anyway".
- The FFmpeg licence: shipping a GPL FFmpeg is fine as a separate program, but the licence and source link must ship with it
  (`licences\THIRD-PARTY.txt`, `licences\FFMPEG-LICENSE.txt`).
