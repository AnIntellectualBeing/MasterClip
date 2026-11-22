"""clipstudio-ytdlp.exe: yt-dlp as its own console program inside the installed app.
Clip Studio runs it hidden (CREATE_NO_WINDOW) and reads its output, like `python -m yt_dlp`."""

import sys

import yt_dlp

if __name__ == "__main__":
    sys.exit(yt_dlp.main())
