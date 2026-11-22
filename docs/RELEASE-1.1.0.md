# MasterClip 1.1.0

Download **MasterClipSetup.exe**, install it, then open **MasterClip** from the Start menu. Stop any current recording and close the older app before upgrading. Existing projects and saved accounts stay in the same user-data location.

- **Continuous clipping:** select Non-stop auto-clipping for a live stream. Start once; each new minute is analysed and the best moments become clips until Stop or the stream ends. Clip now remains an optional extra.
- **New window:** run another source in a separate app window while the first keeps recording. Choose a posting account for each window if needed. Posting speed and format preferences are shared.
- **Auto-post setup upfront:** connect your own Zernio key and channels, then choose clips per hour or daily times. The hourly default is six, adjustable from 1 to 60. This is a queueing preference; platform limits still apply.
- **Independent destinations:** new posts have separate Instagram and YouTube states. A YouTube quota failure leaves Instagram running. Temporary failures wait and retry with stable post identities. Interrupted uploads resume when the app reopens.
- **Visible posting status:** green/red statuses, scheduled times and cancel buttons appear beside the output clips. Reset times are shown only when supplied; otherwise the app shows its next automatic check. Local posted counts are not account-wide quota totals.
- **Format routing:** vertical clips default to Instagram Reels + YouTube Shorts. Long/16:9 clips and long-video compilations go to YouTube only. Configure destinations in Posting setup.
- **HD / Full HD / 4K:** select source-download and export resolution for files and past streams. 4K needs sufficient source detail; cropping and low-resolution footage can require scaling. Live capture stays light.

This Windows release is **unsigned**. Read [Windows installation help](https://github.com/AnIntellectualBeing/MasterClip/blob/main/docs/WINDOWS-HELP.md) if SmartScreen appears. Some Windows configurations block unsigned apps without an override. Support: **anintellectualcompany.contact@gmail.com**.

Keep the PC and at least one MasterClip window running for capture, processing and local retries. Zernio can publish posts that have already been successfully scheduled after the app closes.

Release verification includes automated unit/API tests, two simultaneous real FFmpeg captures with automatic clip generation, simulated platform limits and cancellation, a real 3840×2160 export, and a packaged-EXE import/render smoke test. Social posting tests use simulated accounts; no real posts were published during release testing.
