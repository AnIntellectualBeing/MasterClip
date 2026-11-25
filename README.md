# MasterClip

**Turn any stream into viral Shorts on your own Windows PC. No AI key, no subscription, no watermark.**

Paste a stream link (or search for one), pick a template, and MasterClip finds the hype moments, cuts them into
vertical Shorts with the streamer's cam on top and gameplay below, adds word-by-word captions and a title, and can
post them to Instagram Reels and YouTube Shorts for you.

Get the ready-to-run Windows installer from **[the latest GitHub release](https://github.com/AnIntellectualBeing/MasterClip/releases/latest)**. Product information: [anintellectualcompany.com/software/MasterClip-Tool](https://anintellectualcompany.com/software/MasterClip-Tool/).

Windows shows “Windows protected your PC”? This release is unsigned. See the [installation guide](docs/WINDOWS-HELP.md). Support: **anintellectualcompany.contact@gmail.com**.

### New in 1.6

Everything new is off until you use it, so your clips look the same as before.

- **Layouts + Set it manually:** 11 ready layouts (Full screen, Cam on top, Cam below, Cam in a corner, Round cam, Side by side, Trio, Centred, Two halves, Top bar, Gameplay only). Open **Set it manually** to drag coloured boxes over a frame of the stream: green = gameplay, orange = streamer cam, blue = an extra part. Scroll on a box to zoom.
- **Cards, My brand, Outro & music** (Design step and clip editor): X / Instagram / YouTube / TikTok post cards, a Breaking news frame, your social handles bar, logo, "Clip: name" credit, a background card around the video, an end card or your own outro video, and one song under the clip.
- **Fix captions:** a **Words** tab in the clip editor to change words and timing, import SRT / VTT / ASS, download SRT / VTT. **How words move** (Light up word, Pop each word, Pop the line, One word at a time, Build up, Still line), fonts installed on your PC, and **My caption styles**.
- **Use this on all clips of this video:** one switch in the clip editor puts the whole look on every clip of that video and on its new clips.
- **Title for the first N seconds** (2-60 s) and **Improve heading** (hook questions from what is said).
- **Rate your clips** with stars: after 8 rated moments MasterClip leans to what you like. **Moment mix:** Balanced / Action / Talking.
- **Transcript:** read everything that was said, click the first and last word, make a clip of that part. **Chatbot helper:** let ChatGPT or Gemini find moments in a long video, paste the answer back.
- **📡 Creators:** add a Twitch or YouTube streamer; when they go live MasterClip records and clips chat spikes by itself (one stream at a time, 3 h cap). YouTube live chat now counts for live clipping too.
- **Phone remote** (under **More ▾**): off by default; a PIN page on your home Wi-Fi to start a live stream, press Clip now and download clips from your phone.
- **🖼 Cover** picture for each clip (also sent as the thumbnail when posting), a **New version** note when an update is out (never installs by itself), **Report a problem**, and 4K shown only when the video has it.
- The top bar is shorter: New window, Save folder, Phone remote and Help are under **More ▾**.
- Also carries the unreleased 1.5 Campaigns below.

### New in 1.5

- **💰 Campaigns:** a list of clipping campaigns that pay per views, and sites that run them. **Clip for this** fills in step 1 with the campaign's source, sets the clip length, turns captions on and shows a "Campaign" banner.
- **My submissions:** add the link of a clip you posted, type its views, and see an estimate of what it earns (views / 1000 x the campaign's rate). The total is only an estimate. The platform's own count pays.
- The list comes from our own feed (anintellectualcompany.com/campaigns.json). Offline, MasterClip shows the last saved list.

### New in 1.4

- **Save folder:** click **📁 Save folder** (under **More ▾** since 1.6) and pick any folder, like `D:\MasterClip`. Finished clips and the temporary downloads both go there, so a full C: drive no longer breaks a job.
- MasterClip checks for at least 2 GB free before it starts, and says so in plain words if the disk is full. Other download errors now show the real reason, which helps support.

### New in 1.1

- **Continuous live clipping:** scans each new minute until Stop or the stream ends. No total clip target. Use **New window** for another stream while the first keeps recording.
- **Set up Auto-post once:** select channels, vertical/long destinations, and **clips per hour** (or daily times). Each clip shows its posting time and a cancel button.
- **Independent destinations:** a YouTube limit pauses YouTube and retries later; Instagram continues. Provider messages and next check times appear beside your clips. Counts are posts observed by this app, not invented account quotas.
- **Format routing:** vertical clips default to YouTube Shorts + Instagram Reels. Long/16:9 videos go only to YouTube. Choose which formats post in Posting setup.
- **HD / Full HD / 4K:** choose source-download and output resolution for past streams and files. 4K depends on source detail; cropping or low-resolution sources can require scaling. Live capture stays light for continuous operation.

Keep the PC and at least one MasterClip window running for recording, rendering and retries. Already scheduled Zernio posts can publish after the app closes.

## What it does

- **3 easy steps:** Get video → Design → Find hype moments. Jump back and forth between steps any time.
- **Finds the hype for you:** loud reactions, chat spikes (Twitch chat replay), YouTube "most replayed" and
  what was said. Works fully offline with no AI.
- **Whole 9-hour streams, fast:** it reads the whole stream's replay and chat data without downloading the video,
  then downloads only the hottest few minutes.
- **Live streams:** keep clipping while the streamer is live, with a "Clip now" button and chat-spike clips.
- **Captions that pop:** one-word pop, karaoke, two-line, boxed, bold + red word, two colours with 3D shadow (words
  appear one by one). Hinglish for Hindi streams (Hindi in English letters), plus Kannada, Tamil, Telugu and more.
- **Streamer cam:** found automatically and put on top. With no cam, the Short becomes full gameplay. The gameplay is
  always centred on the action.
- **Viral templates:** streamer top + gameplay, reaction corner cam, podcast split, meme top bar, full gameplay.
  Drag and resize everything on the live preview. Save your own templates.
- **Auto-post:** post through your Zernio account to Instagram and YouTube (vertical clips go up as Shorts). Pick
  "Post now", your own time, or daily time slots. Add up to 30 Zernio accounts and clips take turns across them.
  Turn on **Auto-post every clip** on the first screen, choose clips per hour or daily times, and new clips go out by themselves.
- **Optional AI boost:** better picks and titles with Claude, Codex, Gemini (free) or **Local AI with Ollama** (free,
  private, fast on a graphics card). Speech runs on an NVIDIA GPU automatically when one is available.
- **Your keys stay yours:** keys are stored in Windows Credential Manager, never in files, never sent to us.

## Run from source

Requirements: Windows 10/11, Python 3.11+, FFmpeg (with `ffprobe`) on PATH.

```bat
Setup.cmd    :: once: creates the virtual environment and installs requirements
Start.cmd    :: opens MasterClip
```

Run the tests with `python -m pytest -q`. Build the installer with `packaging\build.ps1` (needs Inno Setup 6).

## Project layout

| Folder | What's inside |
|---|---|
| `clipstudio/` | Engine: download, moment finding, captions, layout, rendering, posting |
| `static/` | The app's screens (HTML/CSS/JS, shown in a desktop window) |
| `packaging/` | Installer build (PyInstaller + Inno Setup) |
| `tests/` | Test suite |

## Licence

MIT, see [LICENSE](LICENSE). Third-party notices are in [THIRD_PARTY.md](THIRD_PARTY.md) and `packaging/licences/`.

Made by [An Intellectual Company](https://anintellectualcompany.com).
