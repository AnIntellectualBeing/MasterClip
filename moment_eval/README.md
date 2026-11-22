# Moment picker test set

8 real 10-minute stretches of GTA streams (4 GTA V roleplay, 4 GTA VI, 7 streamers, Twitch VODs from
the week of 16-22 Nov 2026). The good moments were marked by people: the clips Twitch viewers made
from those VODs (34 viewer clips, merged into 29 moments where clips overlap).

Each case (`cases/*.json`) keeps only what the picker reads, never the video:
loudness and peak per second, on-screen change per 2 s, the local transcript (faster-whisper base,
word times) and the chat replay. Internal test data: these are short excerpts of other people's
streams, so keep this folder out of any public repo and out of the installer.

- `python tools/score_moments.py` - before (frozen old picker, `baseline_v1.py`) vs after vs random.
  `--write` saves RESULTS.md. `--tune-only` scores only the cases used while setting rules.
  `--no-chat` scores the new picker as it works on YouTube links and files (no chat replay).
- `python tools/make_moment_case.py <vod> <start> <end> <id> --marks marks.json` adds a case
  (downloads a 160p slice, measures it, deletes it); `python tools/add_case_chat.py` adds chat.
- `split.json`: "tune" cases were looked at while setting the rules; "held_back" cases were only
  scored (once, at the end). Keep it that way when adding cases.

With ~30 moments the "finding" numbers swing by several points on one hit. Grow the set (20-30 cases,
same tools, more VODs) before trusting small differences.
