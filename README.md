# MasterClip

**Turn any stream into viral Shorts on your own Windows PC. No AI key, no subscription, no watermark.**

Paste a stream link (or search for one), pick a template, and MasterClip finds the hype moments, cuts them into
vertical Shorts with the streamer's cam on top and gameplay below, adds word-by-word captions and a title, and can
post them to Instagram Reels and YouTube Shorts for you.

Get the ready-to-run Windows installer from **[the latest GitHub release](https://github.com/AnIntellectualBeing/MasterClip/releases/latest)**. Product information: [anintellectualcompany.com/software/MasterClip-Tool](https://anintellectualcompany.com/software/MasterClip-Tool/).

Windows shows “Windows protected your PC”? This release is unsigned. See the [installation guide](docs/WINDOWS-HELP.md). Support: **anintellectualcompany.contact@gmail.com**.

### New in 1.4

- **Save folder:** click **📁 Save folder** at the top and pick any folder, like `D:\MasterClip`. Finished clips and the temporary downloads both go there, so a full C: drive no longer breaks a job.
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
